import Foundation
import AudioCore

/// WebSocketASREngine 同时承担两种职责：
///
/// 1. 实现旧的 ASREngine 协议（transcribe(segment:language:)），
///    用于"已经完整切好一段音频，一次性发送识别"的场景——主要是
///    西语通道（远端系统音频）和中文自动 VAD 模式下切好的整段音频。
///    内部实现：开一条短命的 WebSocket 连接，发 start → 音频 → stop，
///    等 final 到达后关闭连接。虽然还是"一段一连接"，但相比 HTTP
///    POST 省去了 multipart 编码 / WAV 文件写盘的开销，且协议统一。
///
/// 2. 实现新的 StreamingASREngine 协议，用于 PTT 按键模式和中文
///    自动模式下的实时流式识别——开一条长连接，音频边到边发，
///    服务端边收边吐 partial，松开按键/VAD 判停时发 stop 拿 final。
///
/// 两种用法共享同一个 WebSocketConnection 实现，只是调用方式不同。
public actor WebSocketASREngine: ASREngine, StreamingASREngine {

    private let wsURL: URL
    private var currentSession: WebSocketStreamingSession?

    /// serverURL 默认走 8081 端口的 WebSocket 服务（对应 server.py 里的 WS_PORT）。
    public init(wsURL: URL = URL(string: "ws://localhost:8081/ws/asr")!) {
        self.wsURL = wsURL
    }

    // ============================================================
    // MARK: - 旧协议兼容：整段音频一次性识别
    // ============================================================

    public func transcribe(segment: Segment, language: String = "zh") async throws -> AsyncThrowingStream<ASRUpdate, Error> {
        let session = WebSocketStreamingSession(wsURL: wsURL, language: language)
        await session.connectAndStart()

        // 整段音频一次性 feed 完，再立刻 finish，等价于旧的"发一次拿结果"语义，
        // 但走的是同一条 WebSocket 长连接协议，不再需要 WAV 编码。
        await session.feed(pcm: segment.audio.pcmData, sampleRate: segment.audio.sampleRate)
        await session.finish()

        return session.updates
    }

    public func abortCurrentTask() async {
        await currentSession?.cancel()
        currentSession = nil
    }

    // ============================================================
    // MARK: - 新协议：流式会话（PTT / 中文流式）
    // ============================================================

    public func startStreamingSession(language: String) async throws -> WebSocketStreamingSession {
        // 如果上一个会话还没结束就开新的，先强制取消，防止两条连接
        // 同时往服务端灌音频造成 cache 混乱（虽然服务端按连接隔离 session，
        // 但客户端侧也应该保证语义上"一次只说一句"）。
        await currentSession?.cancel()

        let session = WebSocketStreamingSession(wsURL: wsURL, language: language)
        await session.connectAndStart()
        currentSession = session
        return session
    }
}

/// 单次会话的 WebSocket 连接封装。
/// 每个实例对应服务端 ASRSession 的一次生命周期：
///   connectAndStart() → 若干次 feed() → finish() → 收到 final → 连接关闭
public actor WebSocketStreamingSession: ASRStreamingSession {

    private let wsURL: URL
    private let language: String
    private var task: URLSessionWebSocketTask?
    private var continuation: AsyncThrowingStream<ASRUpdate, Error>.Continuation?
    private var receiveLoopTask: Task<Void, Never>?
    private var isFinished = false

    // 加 nonisolated：updates 一旦在 init 里被赋值就不再改变，
    // 是一个稳定的不可变句柄，允许外部在任意隔离域下安全读取，
    // 不需要 await 就能拿到这个 stream 并在其上做 for try await 循环。
    nonisolated public let updates: AsyncThrowingStream<ASRUpdate, Error>

    init(wsURL: URL, language: String) {
        self.wsURL = wsURL
        self.language = language

        var capturedContinuation: AsyncThrowingStream<ASRUpdate, Error>.Continuation!
        self.updates = AsyncThrowingStream { continuation in
            capturedContinuation = continuation
        }
        // 注意：Swift 的 AsyncThrowingStream 初始化闭包是同步立即执行的，
        // 所以这里可以安全地把 continuation 提取出来存到 actor 属性里，
        // 不存在"闭包还没跑就被访问"的时序问题。
        self.continuation = capturedContinuation
    }

    /// 建立 WebSocket 连接，并立即发送 start 命令，告知服务端本次会话的语言。
    func connectAndStart() {
        let session = URLSession(configuration: .default)
        let wsTask = session.webSocketTask(with: wsURL)
        self.task = wsTask
        wsTask.resume()

        sendStartCommand()
        startReceiveLoop()
    }

    private func sendStartCommand() {
        let startPayload: [String: Any] = ["cmd": "start", "language": language]
        guard let data = try? JSONSerialization.data(withJSONObject: startPayload),
              let jsonString = String(data: data, encoding: .utf8) else { return }

        task?.send(.string(jsonString)) { error in
            if let error {
                print("⚠️ [WS] 发送 start 命令失败: \(error)")
            }
        }
    }

    // ============================================================
    // MARK: - 音频发送
    // ============================================================

    /// feed 接收任意采样率的 PCM，内部统一重采样到 16kHz 再转 PCM16 二进制发送。
    /// 麦克风原始格式是 48kHz Float32（也可能是 44.1kHz，取决于系统默认设备），
    /// 服务端 Paraformer 要求严格的 16kHz 输入，所以这一步重采样是必需的，
    /// 且刻意放在客户端做（而不是让服务端做），减少服务端 CPU 占用，
    /// 把计算压力分摊到本地设备的空闲核心上。
    public func feed(pcm: [Float], sampleRate: Int) async {
        guard !isFinished, !pcm.isEmpty else { return }

        let resampled = AudioResampler.resample(
            samples: pcm,
            inputSampleRate: sampleRate,
            targetSampleRate: 16000
        )
        guard !resampled.isEmpty else { return }

        let pcm16Data = AudioResampler.floatToPCM16Data(resampled)

        task?.send(.data(pcm16Data)) { error in
            if let error {
                print("⚠️ [WS] 发送音频数据失败: \(error)")
            }
        }
    }

    /// 发送 stop 命令，服务端收到后会 flush 剩余状态并回传 final。
    /// 调用后本 session 视为"逻辑上已结束"，但连接会保持到收到 final
    /// 后才真正关闭（在 receive loop 里处理）。
    public func finish() async {
        guard !isFinished else { return }
        isFinished = true

        let stopPayload: [String: Any] = ["cmd": "stop"]
        guard let data = try? JSONSerialization.data(withJSONObject: stopPayload),
              let jsonString = String(data: data, encoding: .utf8) else { return }

        task?.send(.string(jsonString)) { error in
            if let error {
                print("⚠️ [WS] 发送 stop 命令失败: \(error)")
            }
        }
    }

    /// 强制中断会话：不等待 final，直接关闭连接并结束 stream。
    /// 用于 PTT 快速连续按下/松开，或者上层判断这段话已经没有意义时
    /// （比如用户中途切换了目标语言）。
    public func cancel() async {
        isFinished = true
        receiveLoopTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        continuation?.finish()
        continuation = nil
    }

    // ============================================================
    // MARK: - 接收循环
    // ============================================================

    private func startReceiveLoop() {
        receiveLoopTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let task = await self.task else { break }
                do {
                    let message = try await task.receive()
                    await self.handleIncomingMessage(message)
                } catch {
                    // 连接关闭或网络异常：正常关闭 (goingAway) 不算错误，
                    // 只有非预期错误才 finish(throwing:)
                    await self.handleReceiveError(error)
                    break
                }
            }
        }
    }

    private func handleIncomingMessage(_ message: URLSessionWebSocketTask.Message) async {
        guard case .string(let text) = message,
              let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return
        }

        switch type {
        case "partial":
            let text = (json["text"] as? String) ?? ""
            continuation?.yield(.partial(text, stability: 0.5))

        case "final":
            let text = (json["text"] as? String) ?? ""
            let meta = ASRMeta(confidence: 0.9, durationMs: 0, noSpeechProb: 0)
            continuation?.yield(.final(text, meta: meta))
            continuation?.finish()
            continuation = nil
            task?.cancel(with: .normalClosure, reason: nil)

        case "pong":
            break

        default:
            break
        }
    }

    private func handleReceiveError(_ error: Error) async {
        guard continuation != nil else { return }

        let nsError = error as NSError
        // WebSocket 正常关闭 (客户端主动 cancel 或服务端 normalClosure) 时，
        // URLSession 会抛出一个错误码，不应该当成"真正的失败"往上传播，
        // 否则上层 for try await 会误以为识别失败而报错打断整条流水线。
        let isNormalClosure = nsError.code == 57 || nsError.code == 54 // ENOTCONN / ECONNRESET 常见于对端主动关闭

        if isNormalClosure {
            continuation?.finish()
        } else {
            continuation?.finish(throwing: error)
        }
        continuation = nil
    }
}