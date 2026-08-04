# -*- coding: utf-8 -*-
"""
server_v5.py —— WebSocket 流式重构版（100% 本地部署，无任何云端依赖）

v4 -> v5 核心变化：
  1. 新增 asyncio + websockets 服务，独立监听 ws://localhost:8081/ws/asr，
     与原有 bottle HTTP /inference（8080）并存，共享模型实例和清洗逻辑。
  2. Paraformer 改为真正的 Online Streaming 模式，chunk_size=[0,10,5]，
     每个 WS 连接维护独立 cache 字典（会话状态），连接断开即销毁。
  3. 客户端直接发送 16kHz mono PCM16 二进制帧，服务端攒够一个 stride
     （600ms/9600采样点）就喂一次模型，实时吐 partial；收到
     {"cmd":"stop"} 后 is_final=True flush 剩余 cache，产出 final。
  4. 西语通道在 WS 内部依然是"攒到 stop 才整段扔给 Whisper"，只有 final。
  5. HTTP /inference 路由完整保留，与 v4 行为一致，作为兜底/旧客户端通道。
"""

import os

_CPU_COUNT = os.cpu_count() or 4
_THREAD_CAP = max(2, min(4, _CPU_COUNT // 2))
for _env in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_env, str(_THREAD_CAP))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import io
import re
import json
import time
import uuid
import asyncio
import platform
import tempfile
import threading
import functools
import numpy as np
import torch

torch.set_num_threads(_THREAD_CAP)
torch.set_num_interop_threads(1)

import websockets
from bottle import route, run, request, WSGIRefServer
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer

print = functools.partial(print, flush=True)
DEBUG = True

try:
    from scipy.signal import resample_poly
except ImportError:
    resample_poly = None

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TARGET_SR = 16000
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")

_IS_APPLE_SILICON = (platform.system() == "Darwin" and platform.machine() == "arm64")
_USE_MLX = _IS_APPLE_SILICON and os.environ.get("USE_MLX_WHISPER", "1") != "0"

WS_HOST = os.environ.get("ASR_WS_HOST", "localhost")
WS_PORT = int(os.environ.get("ASR_WS_PORT", "8081"))
HTTP_PORT = int(os.environ.get("ASR_HTTP_PORT", "8080"))

print(f"⏳ [线程策略] CPU核心={_CPU_COUNT}, 每引擎线程上限={_THREAD_CAP}, device={DEVICE}")
print(f"⏳ [平台探测] Apple Silicon={_IS_APPLE_SILICON}, 尝试启用 MLX={_USE_MLX}, whisper模型={WHISPER_MODEL_SIZE}")

# =========================================================
# 1. Whisper 后端（西语，整段推理）
# =========================================================
WHISPER_BACKEND = None
mlx_whisper_mod = None
whisper_model = None
_MLX_MODEL_ID = f"mlx-community/whisper-{WHISPER_MODEL_SIZE}"

if _USE_MLX:
    try:
        print("⏳ [1/2] 正在加载 mlx-whisper 西语 ASR 引擎（Metal 加速）...")
        import mlx_whisper as mlx_whisper_mod
        WHISPER_BACKEND = "mlx"
        print(f"✅ mlx-whisper 就绪！model={_MLX_MODEL_ID}")
    except Exception as e:
        print(f"⚠️ mlx-whisper 加载失败，回退到 faster-whisper: {e}")
        mlx_whisper_mod = None

if WHISPER_BACKEND is None:
    print("⏳ [1/2] 正在加载 faster-whisper 西语 ASR 引擎（CPU int8）...")
    from faster_whisper import WhisperModel
    WHISPER_COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"
    whisper_model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        cpu_threads=_THREAD_CAP,
        num_workers=1,
    )
    WHISPER_BACKEND = "ctranslate2"
    print(f"✅ faster-whisper 就绪！compute_type={WHISPER_COMPUTE_TYPE}")

# =========================================================
# 2. Paraformer 中文流式引擎（online 模型）
# =========================================================
paraformer_model = None

PARAFORMER_CHUNK_SIZE = [0, 10, 5]
PARAFORMER_ENCODER_LOOKBACK = 4
PARAFORMER_DECODER_LOOKBACK = 1
PARAFORMER_STRIDE_SAMPLES = int(PARAFORMER_CHUNK_SIZE[1] * 60 * TARGET_SR / 1000)  # 9600 = 600ms

try:
    print("⏳ [2/2] 正在尝试加载 Paraformer 中文 Online Streaming ASR 引擎...")
    from funasr import AutoModel
    paraformer_model = AutoModel(
        model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
        model_revision="v2.0.4",
        vad_model=None,
        punc_model=None,
        spk_model=None,
        disable_update=True,
        disable_pbar=True,
        device=DEVICE,
        ncpu=_THREAD_CAP,
    )
    print(f"✅ Paraformer 中文流式引擎就绪！chunk_size={PARAFORMER_CHUNK_SIZE}, "
          f"stride={PARAFORMER_STRIDE_SAMPLES}samples(~600ms)")
except Exception as e:
    print(f"⚠️ FunASR 未安装或加载失败，中文识别将自动降级使用 Whisper 兜底: {e}")

zh_lock = threading.Lock()
es_lock = threading.Lock()
LOCK_WAIT_TIMEOUT = 8.0

ws_paraformer_lock = threading.Lock()
ws_whisper_lock = threading.Lock()


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class ThreadedServer(WSGIRefServer):
    def run(self, handler):
        from wsgiref.simple_server import make_server
        self.server = make_server(self.host, self.port, handler, server_class=ThreadedWSGIServer)
        self.server.serve_forever()


# =========================================================
# 3. 幻觉清洗器：正则黑名单 + 数值特征过滤
# =========================================================
HALLUCINATION_PATTERNS = [
    r"字幕\s*by.*", r"字幕.*", r"未经授权.*", r"谢谢观看.*", r"感谢观看.*",
    r"Thanks for watching.*", r"by索兰娅.*", r"索兰娅.*", r"Subtitles.*",
    r"Translated by.*", r"¡Suscríbete!.*", r"Suscríbete.*", r"Amara\.org.*",
    r"Subt[íi]tulos?.*(Amara|comunidad).*", r"[Ss]ubtitled by.*",
    r"点赞.*关注.*", r"下期视频.*", r"www\..*", r"http.*",
    r"Gracias por ver.*", r"[Ss]uscr[íi]bete.*canal.*",
]
_HALLUCINATION_RE = [re.compile(p, flags=re.IGNORECASE) for p in HALLUCINATION_PATTERNS]

WHISPER_COMPRESSION_RATIO_MAX = 2.4
WHISPER_AVG_LOGPROB_MIN = -1.2


def strip_hallucinations(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    for pat in _HALLUCINATION_RE:
        cleaned = pat.sub("", cleaned)
    return cleaned


def collapse_repeats(text: str, max_span: int = 12, rounds: int = 3) -> str:
    if not text:
        return text
    cur = text
    for _ in range(rounds):
        prev = cur
        n = min(max_span, max(1, len(cur) // 2))
        while n >= 1:
            pattern = re.compile(r'(.{%d})\1+' % n, flags=re.DOTALL)
            cur = pattern.sub(r'\1', cur)
            n -= 1
        if cur == prev:
            break
    return cur


def collapse_word_repeats(text: str, max_words: int = 12, rounds: int = 3) -> str:
    tokens = text.split(' ')
    if len(tokens) < 4:
        return text
    cur = tokens
    for _ in range(rounds):
        prev = cur[:]
        n = min(max_words, max(1, len(cur) // 2))
        while n >= 1:
            result = []
            i = 0
            while i < len(cur):
                if i + 2 * n <= len(cur) and cur[i:i + n] == cur[i + n:i + 2 * n]:
                    j = i + n
                    while j + n <= len(cur) and cur[j:j + n] == cur[i:i + n]:
                        j += n
                    result.extend(cur[i:i + n])
                    i = j
                else:
                    result.append(cur[i])
                    i += 1
            cur = result
            n -= 1
        if cur == prev:
            break
    return ' '.join(cur)


def sanitize_and_clean_text(text: str) -> str:
    if not text:
        return ""
    cleaned = strip_hallucinations(text)
    cleaned = collapse_repeats(cleaned)
    if ' ' in cleaned:
        cleaned = collapse_word_repeats(cleaned)
    return cleaned.strip(" ,，.。!！?？'\"")


# =========================================================
# 4. 音频解码（HTTP 路径用，WS 路径直接收 PCM16 不走这里）
# =========================================================
def _resample_to_target(data: np.ndarray, sr: int) -> np.ndarray:
    if sr == TARGET_SR:
        return data
    if resample_poly is not None:
        g = int(np.gcd(int(sr), TARGET_SR))
        return resample_poly(data, TARGET_SR // g, sr // g).astype(np.float32)
    duration = len(data) / sr
    target_len = max(1, int(duration * TARGET_SR))
    return np.interp(
        np.linspace(0, len(data) - 1, target_len),
        np.arange(len(data)),
        data
    ).astype(np.float32)


def decode_audio(raw_bytes: bytes):
    try:
        import soundfile as sf
        data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32", always_2d=False)
        if data.size > 0:
            if data.ndim > 1:
                data = data.mean(axis=1).astype(np.float32)
            return _resample_to_target(data, sr)
    except Exception:
        pass

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        import whisper as _whisper_util
        audio = _whisper_util.load_audio(tmp_path)
        return audio.astype(np.float32)
    except Exception:
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def has_speech(audio: np.ndarray) -> bool:
    if audio is None or audio.size < int(TARGET_SR * 0.25):
        return False
    rms = float(np.sqrt(np.mean(audio ** 2)))
    return rms >= 0.0012


def pcm16_bytes_to_float32(raw_bytes: bytes) -> np.ndarray:
    """WS 路径专用：客户端发的是 16-bit signed little-endian PCM，
    直接转 float32 [-1, 1]，不做任何重采样（客户端已经采样到 16kHz）。"""
    if not raw_bytes:
        return np.zeros(0, dtype=np.float32)
    int16_arr = np.frombuffer(raw_bytes, dtype='<i2')
    return (int16_arr.astype(np.float32) / 32768.0)


# =========================================================
# 5. HTTP 路径：Paraformer / Whisper 整段推理（与 v4 一致）
# =========================================================
def run_paraformer_offline(audio: np.ndarray) -> str:
    """HTTP /inference 路径专用：用非流式方式调用 online 模型也能工作
    （每次都是全新的 cache={}），效果等价于离线推理，仅用于兜底路径。"""
    res = paraformer_model.generate(
        input=audio,
        fs=TARGET_SR,
        cache={},
        is_final=True,
        chunk_size=PARAFORMER_CHUNK_SIZE,
        encoder_chunk_look_back=PARAFORMER_ENCODER_LOOKBACK,
        decoder_chunk_look_back=PARAFORMER_DECODER_LOOKBACK,
        disable_pbar=True,
    )
    if res and len(res) > 0 and 'text' in res[0]:
        return res[0]['text']
    return ""


def _filter_segments(segments_iter):
    parts = []
    for seg in segments_iter:
        text = seg["text"] if isinstance(seg, dict) else seg.text
        cr = seg.get("compression_ratio") if isinstance(seg, dict) else getattr(seg, "compression_ratio", None)
        lp = seg.get("avg_logprob") if isinstance(seg, dict) else getattr(seg, "avg_logprob", None)

        if cr is not None and cr > WHISPER_COMPRESSION_RATIO_MAX:
            if DEBUG:
                print(f"🚫 丢弃高压缩比片段(疑似幻觉复读): {text!r} cr={cr:.2f}")
            continue
        if lp is not None and lp < WHISPER_AVG_LOGPROB_MIN:
            if DEBUG:
                print(f"🚫 丢弃低置信度片段(疑似瞎猜): {text!r} logprob={lp:.2f}")
            continue
        parts.append(text)
    return "".join(parts).strip()


_ANTI_LOOP_KWARGS = dict(
    repetition_penalty=1.3,
    no_repeat_ngram_size=3,
    hallucination_silence_threshold=2.0,
)


def _call_with_graceful_degrade(fn, base_kwargs: dict, extra_kwargs: dict, *args):
    kwargs = {**base_kwargs, **extra_kwargs}
    while True:
        try:
            return fn(*args, **kwargs)
        except TypeError as e:
            msg = str(e)
            removed_any = False
            for k in list(extra_kwargs.keys()):
                if k in kwargs and k in msg:
                    kwargs.pop(k)
                    extra_kwargs.pop(k)
                    removed_any = True
                    if DEBUG:
                        print(f"⚠️ 当前 whisper 后端不支持参数 `{k}`，已自动跳过")
                    break
            if not removed_any:
                raise


def run_whisper(audio: np.ndarray, language: str) -> str:
    lang_kw = {} if (not language or language == "auto") else {"language": language}

    if WHISPER_BACKEND == "mlx":
        base_kwargs = dict(
            path_or_hf_repo=_MLX_MODEL_ID,
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            compression_ratio_threshold=WHISPER_COMPRESSION_RATIO_MAX,
            logprob_threshold=WHISPER_AVG_LOGPROB_MIN,
            **lang_kw,
        )
        result = _call_with_graceful_degrade(
            mlx_whisper_mod.transcribe, base_kwargs, dict(_ANTI_LOOP_KWARGS), audio
        )
        segments = result.get("segments", [])
        if segments:
            return _filter_segments(segments)
        return (result.get("text") or "").strip()

    base_kwargs = dict(
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=WHISPER_COMPRESSION_RATIO_MAX,
        log_prob_threshold=WHISPER_AVG_LOGPROB_MIN,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        **lang_kw,
    )
    segments, _info = _call_with_graceful_degrade(
        whisper_model.transcribe, base_kwargs, dict(_ANTI_LOOP_KWARGS), audio
    )
    return _filter_segments(segments)


# =========================================================
# 6. 启动预热
# =========================================================
def _warmup():
    silence = np.zeros(int(TARGET_SR * 1.0), dtype=np.float32)
    try:
        run_whisper(silence, "es")
        print("✅ Whisper 引擎预热完成")
    except Exception as e:
        print(f"⚠️ Whisper 预热跳过: {e}")
    if paraformer_model is not None:
        try:
            run_paraformer_offline(silence)
            print("✅ Paraformer 引擎预热完成")
        except Exception as e:
            print(f"⚠️ Paraformer 预热跳过: {e}")


_warmup()

# =========================================================
# 7. HTTP 路由（保留，与 v4 完全一致）
# =========================================================
@route('/inference', method='POST')
def inference():
    t0 = time.time()
    upload = request.files.get('file')
    language = request.forms.get('language', default='zh')

    if not upload:
        return {"text": ""}

    raw_bytes = upload.file.read()
    audio = decode_audio(raw_bytes)
    if audio is None or not has_speech(audio):
        return {"text": ""}

    use_paraformer = (language == "zh" and paraformer_model is not None)
    lock = zh_lock if language == "zh" else es_lock

    acquired = lock.acquire(timeout=LOCK_WAIT_TIMEOUT)
    if not acquired:
        print(f"⚠️ [{language}] 获取锁超时({LOCK_WAIT_TIMEOUT}s)，丢弃本次音频")
        return {"text": ""}

    try:
        text = ""
        if use_paraformer:
            text = run_paraformer_offline(audio)
            if DEBUG:
                print(f"🎙️ [Paraformer 中文(HTTP兜底)] 原始识别: {text!r} (耗时 {time.time()-t0:.2f}s)")
        else:
            text = run_whisper(audio, language)
            if DEBUG:
                print(f"🎧 [Whisper({WHISPER_BACKEND}) 西语高精] 原始识别: {text!r} (耗时 {time.time()-t0:.2f}s)")

        cleaned_text = sanitize_and_clean_text(text)

        if cleaned_text and DEBUG:
            print(f"✅ [{language}] 最终有效输出: {cleaned_text!r} (总耗时 {time.time()-t0:.2f}s)")

        return {"text": cleaned_text}

    except Exception as e:
        print(f"❌ ASR 异常: {e}")
        return {"text": ""}
    finally:
        lock.release()


def _run_bottle_server():
    run(host='localhost', port=HTTP_PORT, quiet=True, server=ThreadedServer)


# =========================================================
# 8. WebSocket 流式会话状态机
# =========================================================
class ASRSession:
    """每个 WS 连接对应一个会话实例，持有独立的 Paraformer cache
    和音频缓冲区。绝不允许跨连接共享，否则流式解码状态会互相污染。"""

    def __init__(self, language: str):
        self.language = language
        self.pcm_buffer = np.zeros(0, dtype=np.float32)
        self.full_audio_accum = np.zeros(0, dtype=np.float32)  # 供 Whisper 整段推理 / 兜底重推
        self.paraformer_cache = {}
        self.closed = False
        self.last_partial_text = ""
        self.total_bytes_received = 0

    def feed_pcm16(self, raw_bytes: bytes):
        chunk = pcm16_bytes_to_float32(raw_bytes)
        self.pcm_buffer = np.concatenate([self.pcm_buffer, chunk])
        self.full_audio_accum = np.concatenate([self.full_audio_accum, chunk])
        self.total_bytes_received += len(raw_bytes)

    def pop_stride_chunk(self):
        """攒够一个 PARAFORMER_STRIDE_SAMPLES 就切一块出来喂模型，
        不足的留在 buffer 里等下一次数据到达再拼。"""
        if len(self.pcm_buffer) < PARAFORMER_STRIDE_SAMPLES:
            return None
        chunk = self.pcm_buffer[:PARAFORMER_STRIDE_SAMPLES]
        self.pcm_buffer = self.pcm_buffer[PARAFORMER_STRIDE_SAMPLES:]
        return chunk

    def pop_remaining_as_final_chunk(self):
        """stop 时把 buffer 里剩下的尾巴（可能不足一个 stride）取出来，
        作为最后一次 is_final=True 调用的输入，哪怕是 0 长度也要调用一次
        以便 flush 模型内部残留状态。"""
        chunk = self.pcm_buffer
        self.pcm_buffer = np.zeros(0, dtype=np.float32)
        return chunk


def run_paraformer_streaming_chunk(session: ASRSession, chunk: np.ndarray, is_final: bool) -> str:
    """喂一个 stride 长度的音频块进 Paraformer online 模型，
    返回该次调用产出的增量文本（不是全量文本，FunASR online 模式下
    generate() 返回的就是这次 chunk 对应的新增文本片段）。"""
    with ws_paraformer_lock:
        res = paraformer_model.generate(
            input=chunk,
            cache=session.paraformer_cache,
            is_final=is_final,
            chunk_size=PARAFORMER_CHUNK_SIZE,
            encoder_chunk_look_back=PARAFORMER_ENCODER_LOOKBACK,
            decoder_chunk_look_back=PARAFORMER_DECODER_LOOKBACK,
            fs=TARGET_SR,
            disable_pbar=True,
        )
    if res and len(res) > 0 and 'text' in res[0]:
        return res[0]['text']
    return ""


def run_whisper_final(session: ASRSession) -> str:
    """西语通道：session 关闭时，把累积的全部音频一次性扔给 Whisper。"""
    audio = session.full_audio_accum
    if audio.size == 0 or not has_speech(audio):
        return ""
    with ws_whisper_lock:
        return run_whisper(audio, session.language)


async def handle_asr_session(websocket):
    """单个 WebSocket 连接的完整生命周期处理。
    协议：
      1. 客户端连接后先发一个 JSON 文本帧 {"cmd":"start","language":"zh"}
      2. 之后连续发二进制 PCM16 (16kHz mono little-endian) 帧
      3. 客户端发 {"cmd":"stop"} 表示本段音频结束（VAD 判停 或 PTT 松开）
      4. 服务端持续推送 {"type":"partial","text":...} 与
         {"type":"final","text":...}
      5. 客户端可以在收到一次 final 后，复用同一条连接继续发下一段
         （发新的 {"cmd":"start",...} 重置会话状态），减少反复握手开销；
         也可以每段话一条新连接，两种用法服务端都支持。
    """
    session: ASRSession = None
    peer = websocket.remote_address
    print(f"🔌 [WS] 新连接建立: {peer}")

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    cmd_obj = json.loads(message)
                except json.JSONDecodeError:
                    continue

                cmd = cmd_obj.get("cmd")

                if cmd == "start":
                    language = cmd_obj.get("language", "zh")
                    session = ASRSession(language=language)
                    if DEBUG:
                        print(f"🎬 [WS] 会话开始 language={language} peer={peer}")

                elif cmd == "stop":
                    if session is None:
                        continue
                    await _finalize_session(websocket, session)
                    session = None

                elif cmd == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))

            elif isinstance(message, bytes):
                if session is None:
                    # 客户端还没发 start 就发了音频，直接忽略这批数据，
                    # 防止无主音频污染下一个会话。
                    continue

                session.feed_pcm16(message)

                if session.language == "zh" and paraformer_model is not None:
                    await _drain_paraformer_partials(websocket, session)
                # 西语通道不做 partial，只在 stop 时统一跑一次 Whisper。

    except websockets.exceptions.ConnectionClosed:
        if DEBUG:
            print(f"🔌 [WS] 连接已断开: {peer}")
    except Exception as e:
        print(f"❌ [WS] 会话异常: {e}")
    finally:
        session = None


async def _drain_paraformer_partials(websocket, session: ASRSession):
    """把 buffer 里能凑够整数个 stride 的部分都喂进模型，
    每次调用产出的增量文本拼接后作为 partial 推给前端。
    用 asyncio.to_thread 把同步的 generate() 调用丢到线程池，
    避免阻塞 asyncio 事件循环导致其他连接卡顿。"""
    accumulated_partial_delta = ""
    while True:
        chunk = session.pop_stride_chunk()
        if chunk is None:
            break
        delta_text = await asyncio.to_thread(
            run_paraformer_streaming_chunk, session, chunk, False
        )
        if delta_text:
            accumulated_partial_delta += delta_text

    if accumulated_partial_delta:
        session.last_partial_text += accumulated_partial_delta
        cleaned_preview = strip_hallucinations(session.last_partial_text)
        await websocket.send(json.dumps({
            "type": "partial",
            "text": cleaned_preview
        }, ensure_ascii=False))
        if DEBUG:
            print(f"📝 [WS partial][{session.language}] {cleaned_preview!r}")


async def _finalize_session(websocket, session: ASRSession):
    """收到 stop 命令：
      - 中文：把 buffer 剩余尾块用 is_final=True 喂进去 flush 掉模型残留
        状态，拿到最后一段增量文本，与之前累积的 partial 拼接成完整文本，
        再走一遍幻觉清洗/去重后作为 final 推送。
      - 西语：把全部累积音频一次性扔给 Whisper。
    """
    t0 = time.time()
    final_text = ""

    if session.language == "zh" and paraformer_model is not None:
        tail_chunk = session.pop_remaining_as_final_chunk()
        tail_delta = await asyncio.to_thread(
            run_paraformer_streaming_chunk, session, tail_chunk, True
        )
        raw_full_text = session.last_partial_text + (tail_delta or "")
        final_text = sanitize_and_clean_text(raw_full_text)
        if DEBUG:
            print(f"🏁 [WS final][zh/Paraformer] {final_text!r} (耗时 {time.time()-t0:.2f}s)")

    elif session.language == "zh":
        # Paraformer 未加载成功，降级用 Whisper 兜底（虽然不是它的强项，
        # 但至少保证功能不中断）。
        raw_text = await asyncio.to_thread(run_whisper, session.full_audio_accum, "zh")
        final_text = sanitize_and_clean_text(raw_text)
        if DEBUG:
            print(f"🏁 [WS final][zh/Whisper兜底] {final_text!r} (耗时 {time.time()-t0:.2f}s)")

    else:
        raw_text = await asyncio.to_thread(run_whisper_final, session)
        final_text = sanitize_and_clean_text(raw_text)
        if DEBUG:
            print(f"🏁 [WS final][{session.language}/Whisper] {final_text!r} (耗时 {time.time()-t0:.2f}s)")

    await websocket.send(json.dumps({
        "type": "final",
        "text": final_text
    }, ensure_ascii=False))


async def _run_websocket_server():
    print(f"🚀 [WS] ASR WebSocket 服务启动于 ws://{WS_HOST}:{WS_PORT}/ws/asr")
    async with websockets.serve(
        handle_asr_session,
        WS_HOST,
        WS_PORT,
        max_size=None,       # 音频流可能持续较长时间，不限制单帧大小
        ping_interval=20,
        ping_timeout=20,
    ):
        await asyncio.Future()  # 永久阻塞，直到进程退出


if __name__ == "__main__":
    # bottle HTTP 服务放到独立线程里跑（它自己是阻塞式 serve_forever），
    # 主线程跑 asyncio 事件循环负责 WebSocket。两者共享同一批模型实例
    # 和线程锁，互不冲突。
    http_thread = threading.Thread(target=_run_bottle_server, daemon=True)
    http_thread.start()
    print(f"🚀 [HTTP] 兜底 /inference 服务已启动于 http://localhost:{HTTP_PORT}")

    try:
        asyncio.run(_run_websocket_server())
    except KeyboardInterrupt:
        print("👋 服务已停止")