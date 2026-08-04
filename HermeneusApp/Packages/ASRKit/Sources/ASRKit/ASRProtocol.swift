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

public enum ASRUpdate: Sendable {
    case partial(String, stability: Float)
    case final(String, meta: ASRMeta)
}

public protocol ASREngine: Actor {
    func transcribe(segment: Segment, language: String) async throws -> AsyncThrowingStream<ASRUpdate, Error>
    func abortCurrentTask() async
}
