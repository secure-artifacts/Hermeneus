import Foundation
import AudioCore

public struct ASRMeta: Sendable {
    public let confidence: Float
    public let durationMs: Double
    public let noSpeechProb: Float

    public init(confidence: Float, durationMs: Double, noSpeechProb: Float = 0.0) {
        self.confidence = confidence
        self.durationMs = durationMs
        self.noSpeechProb = noSpeechProb
    }
}

/// ASRUpdate 新增 partial 分支的语义说明：
/// - .partial(text, stability): 流式识别过程中的中间态文本。
///   stability 目前后端未真正计算置信度分层，统一传 0.5 占位，
///   预留给未来接入 Paraformer 的 timestamp / confidence 字段后细化。
///   UI 层应将 partial 文本以"字随嘴走"的方式实时渲染（通常用较浅的颜色
///   或斜体区分于 final），但绝不能拿 partial 触发翻译。
/// - .final(text, meta): 一段话的最终识别结果，只有这个分支才应该
///   触发下游的 Ollama 翻译调用。
public enum ASRUpdate: Sendable {
    case partial(String, stability: Float)
    case final(String, meta: ASRMeta)
}

/// ASREngine 协议保持向后兼容：transcribe(segment:language:) 仍然保留，
/// 给"整段音频一次性识别"的调用方式使用（西语通道、或自动 VAD 模式下
/// 已经完整切好的一段音频）。
///
/// 新增 StreamingASREngine 协议专门给"边说边喂音频"的流式场景使用
/// （PTT 按键模式 + 中文流式 Paraformer），二者不冲突，
/// WebSocketASREngine 会同时实现这两个协议。
public protocol ASREngine: Actor {
    func transcribe(segment: Segment, language: String) async throws -> AsyncThrowingStream<ASRUpdate, Error>
    func abortCurrentTask() async
}

/// 流式会话句柄：调用方通过它持续 feed 音频帧，并在需要截断时调用
/// finish()。所有 partial/final 事件从 updates 这个异步流里读取。
///
/// 生命周期：
///   1. 调用 StreamingASREngine.startStreamingSession(language:) 拿到 session
///   2. 音频到达时循环调用 session.feed(pcm:sampleRate:)
///   3. VAD 判停 或 PTT 松开时调用 session.finish()
///   4. session.updates 会在收到 final 事件后自然结束（continuation.finish()）
public protocol ASRStreamingSession: Actor {
    nonisolated var updates: AsyncThrowingStream<ASRUpdate, Error> { get }
    func feed(pcm: [Float], sampleRate: Int) async
    func finish() async
    func cancel() async
}

public protocol StreamingASREngine: Actor {
    associatedtype Session: ASRStreamingSession
    func startStreamingSession(language: String) async throws -> Session
}