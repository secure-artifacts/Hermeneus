import Foundation

public struct TranslationContext: Sendable {
    public let sourceText: String
    public let translatedText: String
    
    public init(sourceText: String, translatedText: String) {
        self.sourceText = sourceText
        self.translatedText = translatedText
    }
}

public enum TranslationUpdate: Sendable {
    case delta(String)
    case clauseCommitted(String)
    case final(String)
}

public protocol TranslationEngine: Sendable {
    func translate(
        text: String,
        contextHistory: [TranslationContext],
        targetLanguage: String
    ) async throws -> AsyncThrowingStream<TranslationUpdate, Error>
}
