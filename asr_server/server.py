# -*- coding: utf-8 -*-
"""
server_v4.py —— 修复 v3 引入的"长缓冲音频卡死复读"回归

v3 -> v4 变化：
  1. 【修复回归】重新打开 vad_filter，但 min_silence_duration_ms 调小（500ms）。
     v3 关掉它是错误判断：Swift 端 ContinuousStreamSegmenter 最长可以缓冲
     30 秒才强制切句，30 秒长音频内部完全可能夹杂几段小停顿/弱语音，
     Whisper 解码到这些片段如果没有 VAD 跳过，容易陷入原地复读同一短语
     的经典退化模式（你截图里 "Es lo que nos diga." 连续复读四五遍就是
     这个问题）。500ms 的阈值足够跳过死寂，又不会像最早遇到的那样把正常
     语速中的短暂停顿也切掉。
  2. 新增解码期防复读参数：repetition_penalty / no_repeat_ngram_size /
     hallucination_silence_threshold —— 这几个是 faster-whisper /
     mlx-whisper 近几个版本专门为治这个 bug 加的参数，从解码源头抑制
     "卡在一个短语打转"，比事后用正则硬切更可靠。做了版本兼容降级：
     如果你装的库版本不支持某个参数，会自动去掉该参数重试，不会直接崩。
  3. 新增词级别去重 collapse_word_repeats()：旧的 collapse_repeats() 只按
     字符跨度做多轮塌缩，上限 12 个字符，像 "Es lo que nos diga."(19字符)
     这种短语级重复完全漏网。这次西语（含空格的文本）额外跑一遍按词去重。
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
import time
import platform
import tempfile
import threading
import functools
import numpy as np
import torch

torch.set_num_threads(_THREAD_CAP)
torch.set_num_interop_threads(1)

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

print(f"⏳ [线程策略] CPU核心={_CPU_COUNT}, 每引擎线程上限={_THREAD_CAP}, device={DEVICE}")
print(f"⏳ [平台探测] Apple Silicon={_IS_APPLE_SILICON}, 尝试启用 MLX={_USE_MLX}, whisper模型={WHISPER_MODEL_SIZE}")

# =========================================================
# 1. Whisper 后端
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
# 2. Paraformer 中文引擎
# =========================================================
paraformer_model = None
try:
    print("⏳ [2/2] 正在尝试加载 Paraformer 中文 Realtime ASR 引擎...")
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
    print("✅ Paraformer 中文极速引擎就绪！")
except Exception as e:
    print(f"⚠️ FunASR 未安装或加载失败，中文识别将自动降级使用 Whisper 兜底: {e}")

zh_lock = threading.Lock()
es_lock = threading.Lock()
LOCK_WAIT_TIMEOUT = 8.0


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


# ---- 字符级去重（中文 / 无空格文本，如 "确确确"、"字了字了"）----
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


# ---- 词级别去重（西语等有空格分词的语言，处理短语级复读如
#      "Es lo que nos diga. Es lo que nos diga."）----
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
# 4. 音频解码
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


# =========================================================
# 5. 两条 ASR 通道
# =========================================================
def run_paraformer(audio: np.ndarray) -> str:
    res = paraformer_model.generate(
        input=audio,
        fs=TARGET_SR,
        disable_pbar=True,
        batch_size_s=300,
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


# 解码期防复读参数。不同版本的 faster-whisper/mlx-whisper 支持程度不一，
# 用"报 TypeError 就摘掉那个参数重试"的方式做兼容降级，保证老版本库也能跑。
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

    # ctranslate2 (faster-whisper) 路径
    base_kwargs = dict(
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=WHISPER_COMPRESSION_RATIO_MAX,
        log_prob_threshold=WHISPER_AVG_LOGPROB_MIN,
        # 重新打开，但阈值调小：只用来跳过长缓冲(最长30s)音频内部的死寂
        # 片段，防止复读退化，不会像完全交给它切句那样误伤正常停顿
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
            run_paraformer(silence)
            print("✅ Paraformer 引擎预热完成")
        except Exception as e:
            print(f"⚠️ Paraformer 预热跳过: {e}")


_warmup()

# =========================================================
# 7. 路由
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
            text = run_paraformer(audio)
            if DEBUG:
                print(f"🎙️ [Paraformer 中文极速] 原始识别: {text!r} (耗时 {time.time()-t0:.2f}s)")
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


if __name__ == "__main__":
    run(host='localhost', port=8080, quiet=True, server=ThreadedServer)