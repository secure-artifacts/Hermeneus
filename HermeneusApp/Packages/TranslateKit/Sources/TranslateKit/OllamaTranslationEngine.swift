import Foundation

public actor OllamaTranslationEngine: TranslationEngine {
    private let endpoint: URL
    // 改动 1：modelName 从 `let` 改成 `var`。
    // 之前硬编码 "qwen2.5:7b" 且不可变，改模型只能重新初始化整个引擎
    // (等于重开一次翻译会话)。现在允许在运行期通过 updateModel(_:) 热切换。
    private var modelName: String
    private let numThread: Int?
    private let requestTimeout: TimeInterval
    private let keepAlive: String
    private let onTermCorrectionExtracted: (@Sendable (_ original: String, _ corrected: String) -> Void)?
    private let contextPromptProvider: (@Sendable (_ text: String) async -> String)?

    public init(
        endpoint: URL = URL(string: "http://localhost:11434/api/generate")!,
        // 改动 2：默认值改为 "qwen2.5:3b"，仅作为“探测/调用方都没配置时”的
        // 最终兜底。正常路径下，App 启动时应该由 OllamaModelManager.resolvePreferredModel()
        // 决定真正使用的模型，再通过 updateModel(_:) 或下面的 init 参数注入，
        // 而不是依赖这个默认值。
        modelName: String = "qwen2.5:3b",
        numThread: Int? = nil,
        requestTimeout: TimeInterval = 60.0,
        keepAlive: String = "30m",
        onTermCorrectionExtracted: (@Sendable (_ original: String, _ corrected: String) -> Void)? = nil,
        contextPromptProvider: (@Sendable (_ text: String) async -> String)? = nil
    ) {
        self.endpoint = endpoint
        self.modelName = modelName
        self.numThread = numThread
        self.requestTimeout = requestTimeout
        self.keepAlive = keepAlive
        self.onTermCorrectionExtracted = onTermCorrectionExtracted
        self.contextPromptProvider = contextPromptProvider
    }

    // 改动 3：新增热切换入口。OllamaModelStore.onModelChanged 回调应该
    // 调用 `await engine.updateModel(to: newName)`，下一次 translate()
    // 就会用新模型发请求，不需要重建 actor、也不打断正在进行的会话状态
    // （比如 contextHistory 仍在调用方手里，天然保留）。
    public func updateModel(to newModelName: String) {
        guard newModelName != modelName else { return }
        modelName = newModelName
    }

    public func translate(
        text: String,
        contextHistory: [TranslationContext],
        targetLanguage: String
    ) async throws -> AsyncThrowingStream<TranslationUpdate, Error> {
        let trimmedInput = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedInput.isEmpty else {
            return AsyncThrowingStream { continuation in
                continuation.yield(.final(""))
                continuation.finish()
            }
        }

        let prompt = await buildPrompt(text: trimmedInput, contextHistory: contextHistory, targetLanguage: targetLanguage)

        // request 在这里只声明一次、是不可变的 let（内部构造细节封装进
        // buildOllamaRequest，函数内部用 var 组装，返回时已经是完整的值），
        // 这样 Task{} 闭包捕获的天然就是一个 Sendable 的 let，满足严格并发检查。
        // 注意：buildOllamaRequest 读取的是调用这一刻的 self.modelName，
        // 所以即便 updateModel(to:) 在两次 translate() 之间被调用，
        // 每次请求都会带上"当时"最新的模型名，不存在陈旧闭包捕获的问题。
        let request = try buildOllamaRequest(prompt: prompt)

        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                        continuation.finish(throwing: NSError(
                            domain: "OllamaError",
                            code: -1,
                            userInfo: [NSLocalizedDescriptionKey: "Ollama 服务返回非 200 状态"]
                        ))
                        return
                    }

                    var fullText = ""
                        
                        for try await line in bytes.lines {
                            guard let data = line.data(using: .utf8),
                                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                                  let responseToken = json["response"] as? String, !responseToken.isEmpty else { continue }
                            
                            // 极简流式解析，抛弃所有死板的标签等待，直接渲染文字！
                            let tokenToUse = responseToken.replacingOccurrences(of: "[备选]", with: "\n(备选：").replacingOccurrences(of: "备选：", with: "\n(备选：")
                            fullText += tokenToUse
                            continuation.yield(.delta(tokenToUse))
                        }
                        
                        let trimmedFinal = fullText.trimmingCharacters(in: .whitespacesAndNewlines)
                        continuation.yield(.final(trimmedFinal))
                        continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }

            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    
    private func buildOllamaRequest(prompt: String) throws -> URLRequest {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = requestTimeout

        var options: [String: Any] = [
            "temperature": 0.05,
            "top_k": 5,
            "top_p": 0.9,
            "num_ctx": 1536,
            "num_predict": 220,
            "repeat_penalty": 1.30,
            "stop": ["\nText:", "\nTranslation:", "\n【当前】", "\n【上文】"]
        ]
        if let numThread {
            options["num_thread"] = numThread
        }

        let body: [String: Any] = [
            "model": modelName,
            "prompt": prompt,
            "stream": true,
            "keep_alive": keepAlive,
            "options": options
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return request
    }

    private func sanitizeTokens(_ text: String) -> String {
        var result = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let trashPatterns = [
            "[译文]", "[备选]", "备选直译中文:", "备选直译中文：",
            "(仅在有歧义时输出)", "（仅在有歧义时输出）",
            "(仅在有歧义时输出）。", "（仅在有歧义时输出）。"
        ]
        for pattern in trashPatterns {
            result = result.replacingOccurrences(of: pattern, with: "")
        }
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private enum ParsePhase {
        case beforeMarker
        case streaming
    }

    private func processSemanticRepairIfNeeded(rawRepairSection: String, repairMarker: String, originalText: String) {
        guard let corrected = extractCorrectedText(
            from: rawRepairSection,
            repairMarker: repairMarker,
            originalText: originalText
        ) else {
            return
        }

        if let termDiff = extractTermLevelDiff(original: originalText, corrected: corrected) {
            onTermCorrectionExtracted?(termDiff.original, termDiff.corrected)
        }

        guard DebugFlags.logSemanticRepair else { return }
        print("🔧 [语义纠错] 原文: \(originalText) → 修正为: \(corrected)")
    }

    private func extractCorrectedText(from rawRepairSection: String, repairMarker: String, originalText: String) -> String? {
        guard let tagRange = rawRepairSection.range(of: repairMarker) else { return nil }
        let corrected = String(rawRepairSection[tagRange.upperBound...])
            .trimmingCharacters(in: .whitespacesAndNewlines)

        let placeholders: Set<String> = ["无", "无误", "none", "None", "N/A", "-", ""]
        guard !placeholders.contains(corrected), corrected != originalText else { return nil }
        return corrected
    }

    private func extractTermLevelDiff(original: String, corrected: String) -> (original: String, corrected: String)? {
        let originalChars = Array(original)
        let correctedChars = Array(corrected)

        var prefixLen = 0
        while prefixLen < originalChars.count,
              prefixLen < correctedChars.count,
              originalChars[prefixLen] == correctedChars[prefixLen] {
            prefixLen += 1
        }

        var suffixLen = 0
        while suffixLen < originalChars.count - prefixLen,
              suffixLen < correctedChars.count - prefixLen,
              originalChars[originalChars.count - 1 - suffixLen] == correctedChars[correctedChars.count - 1 - suffixLen] {
            suffixLen += 1
        }

        let originalDiffRange = prefixLen..<(originalChars.count - suffixLen)
        let correctedDiffRange = prefixLen..<(correctedChars.count - suffixLen)

        let trimSet = CharacterSet.whitespacesAndNewlines.union(.punctuationCharacters)
        let originalTerm = String(originalChars[originalDiffRange]).trimmingCharacters(in: trimSet)
        let correctedTerm = String(correctedChars[correctedDiffRange]).trimmingCharacters(in: trimSet)

        guard !originalTerm.isEmpty, !correctedTerm.isEmpty else { return nil }

        let totalLen = max(originalChars.count, correctedChars.count)
        let diffLen = max(originalTerm.count, correctedTerm.count)
        guard totalLen > 0, Double(diffLen) / Double(totalLen) <= 0.5 else { return nil }

        return (originalTerm, correctedTerm)
    }

    private func stripLeadingRepairTag(_ text: String, repairMarker: String) -> String {
        guard let tagRange = text.range(of: repairMarker) else {
            return text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return String(text[tagRange.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func detectSourceLanguageHint(from text: String) -> String {
        let hasCJK = text.unicodeScalars.contains { scalar in
            (0x4E00...0x9FFF).contains(scalar.value) || (0x3400...0x4DBF).contains(scalar.value)
        }
        return hasCJK ? "中文" : "西班牙语"
    }

        private func buildPrompt(
        text: String,
        contextHistory: [TranslationContext],
        targetLanguage: String
    ) async -> String {
        let sourceLanguageHint = detectSourceLanguageHint(from: text)
        let isTargetChinese = targetLanguage == "中文"

        let rolePrompt: String
        if isTargetChinese {
            rolePrompt = """
            你是一名精通\(sourceLanguageHint)和中文的资深同声传译员。
            请把输入的\(sourceLanguageHint)语音转写文本翻译为地道、自然、口语化的中文。

            【硬性要求】
            1. 绝对不要输出任何\(sourceLanguageHint)原文！绝对不要输出 [译文]、[备选] 等任何标签或说明提示词！
            2. 默认只输出 1 句最地道自然的中文翻译。
            3. 只有当原文存在严重的歧义或多义词时，才允许在主译文末尾用括号补充，例如：主译文 (或：备选译法)。
            """
        } else {
            rolePrompt = """
            你是一名精通中文和西班牙语的资深同声传译员。
            请把输入的中文翻译为地道、口语化的西班牙语。

            【硬性要求】
            1. 只输出最终的西班牙语译文文本，绝对不要输出任何中文解释、注释或思考草稿（绝对不能出现"应该是..."、"也可以是..."等内容）。
            2. 必须且仅翻译【当前原文】！
            """
        }

        let contextBlock = await contextPromptProvider?(text) ?? ""

        var historyBlock = ""
        let recentHistory = contextHistory.suffix(2)
        if !recentHistory.isEmpty {
            let lines = recentHistory.map { "- \($0.sourceText) → \($0.translatedText)" }.joined(separator: "\n")
            historyBlock = "\n\n【上文背景】\n\(lines)"
        }

        return "\(rolePrompt)\(contextBlock)\(historyBlock)\n\n【当前原文】\n\(text)\n\n【翻译】"
    }
}

private enum DebugFlags {
    static var logSemanticRepair: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}
