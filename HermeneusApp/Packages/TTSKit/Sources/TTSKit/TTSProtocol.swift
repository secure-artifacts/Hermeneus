import Foundation

public protocol TTSEngine: Sendable {
    func speak(text: String, language: String) async throws
    func stopPlayout() async
}

public protocol SpeechScheduler: Sendable {
    func enqueueClause(text: String, language: String) async
    func stop() async
}
