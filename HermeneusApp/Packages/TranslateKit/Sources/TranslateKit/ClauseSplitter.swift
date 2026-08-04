import Foundation

public final class ClauseSplitter {
    private var buffer: String = ""
    private var tokenCount: Int = 0
    private let punctuationSet: CharacterSet = CharacterSet(charactersIn: "，。！？；,.!?;")
    
    public init() {}
    
    /// 输入流式 token，若满足小句条件则返回切割出的小句字符串
    public func feed(token: String) -> String? {
        buffer += token
        tokenCount += 1
        
        let trimmed = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        
        // 判定条件 1：遇到了标点符号
        if let lastChar = trimmed.last, lastChar.unicodeScalars.allSatisfy({ punctuationSet.contains($0) }) {
            let clause = trimmed
            reset()
            return clause
        }
        
        // 判定条件 2：Token 数量达到了上限 (12 个 Token)
        if tokenCount >= 12 {
            let clause = trimmed
            reset()
            return clause
        }
        
        return nil
    }
    
    /// 翻译流结束时，清空并吐出剩余的文本
    public func flush() -> String? {
        let trimmed = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        reset()
        return trimmed.isEmpty ? nil : trimmed
    }
    
    private func reset() {
        buffer = ""
        tokenCount = 0
    }
}
