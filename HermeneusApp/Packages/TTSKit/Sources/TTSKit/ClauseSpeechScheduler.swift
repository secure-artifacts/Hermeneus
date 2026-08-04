import Foundation

public actor ClauseSpeechScheduler: SpeechScheduler {
    private let ttsEngine: TTSEngine
    private var clauseQueue: [(text: String, language: String)] = []
    private var isProcessing: Bool = false
    
    public init(ttsEngine: TTSEngine) {
        self.ttsEngine = ttsEngine
    }
    
    public func enqueueClause(text: String, language: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        clauseQueue.append((trimmed, language))
        await processQueue()
    }
    
    public func stop() async {
        clauseQueue.removeAll()
        await ttsEngine.stopPlayout()
        isProcessing = false
    }
    
    private func processQueue() async {
        guard !isProcessing else { return }
        isProcessing = true
        
        while !clauseQueue.isEmpty {
            let item = clauseQueue.removeFirst()
            try? await ttsEngine.speak(text: item.text, language: item.language)
        }
        
        isProcessing = false
    }
}
