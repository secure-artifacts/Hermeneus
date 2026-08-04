import Foundation

public enum ConversationScene: String, Sendable, CaseIterable {
    case generalLifestyle = "GENERAL_LIFESTYLE"
    case guiOperations = "GUI_OPERATIONS"

    var displayName: String {
        switch self {
        case .generalLifestyle: return "日常生活与信仰交流"
        case .guiOperations:    return "软件操作指导"
        }
    }
}

public struct ContextSnapshot: Sendable, Equatable {
    public let scene: ConversationScene
    public let sceneScore: Double
    public let glossaryCount: Int
    public let recentTerms: [(original: String, corrected: String)]

    public static func == (lhs: ContextSnapshot, rhs: ContextSnapshot) -> Bool {
        lhs.scene == rhs.scene
            && lhs.glossaryCount == rhs.glossaryCount
            && abs(lhs.sceneScore - rhs.sceneScore) < 0.001
            && lhs.recentTerms.map(\.original) == rhs.recentTerms.map(\.original)
            && lhs.recentTerms.map(\.corrected) == rhs.recentTerms.map(\.corrected)
    }
}

private struct TermEntry {
    let original: String
    var corrected: String
    var sequence: UInt64
    var hitCount: Int
}

public actor ConversationContextEngine {
    public struct Configuration: Sendable {
        public var maxGlossarySize: Int = 24
        public var maxTermsInPrompt: Int = 8
        public var sceneScoreDecay: Double = 0.7
        public var guiEnterThreshold: Double = 3.0
        public var guiExitThreshold: Double = 1.0
        public var sceneScoreCeiling: Double = 6.0

        public init() {}
    }

    private var config: Configuration
    private var currentScene: ConversationScene = .generalLifestyle
    private var sceneScore: Double = 0
    private var glossary: [String: TermEntry] = [:]
    private var sequenceCounter: UInt64 = 0

    public init(configuration: Configuration = Configuration()) {
        self.config = configuration
    }

    private static let guiKeywordWeights: [String: Double] = [
        "快捷键": 2.0, "组合键": 2.0,
        "Command": 2.0, "command": 2.0, "⌘": 2.0,
        "Control": 2.0, "control": 2.0, "Ctrl": 2.0, "ctrl": 2.0, "⌃": 2.0,
        "Option": 2.0, "option": 2.0, "Alt": 2.0, "⌥": 2.0,
        "Shift": 2.0, "shift": 2.0, "⇧": 2.0,
        "Escape": 2.0, "Esc": 2.0, "退格": 2.0, "Backspace": 2.0,
        "Enter": 2.0, "Return": 2.0, "回车": 2.0,
        "右上角": 2.0, "左上角": 2.0, "右下角": 2.0, "左下角": 2.0,
        "对话框": 2.0, "弹窗": 2.0, "下拉框": 2.0,
        "复制粘贴": 2.0, "剪切": 2.0, "撤销": 1.5, "重做": 1.5,
        "截图": 1.5, "快捷指令": 1.5,
        "点击": 1.0, "单击": 1.0, "双击": 1.0, "右键": 1.0,
        "按下": 1.0, "长按": 1.0, "勾选": 1.0,
        "表格": 1.0, "单元格": 1.0, "行列": 1.0, "筛选": 1.0, "排序": 1.0,
        "输入框": 1.5, "文本框": 1.5,
        "窗口": 1.0, "图标": 1.5, "页签": 1.0, "标签页": 1.0,
        "设置": 1.0, "偏好设置": 2.0, "系统设置": 2.0,
        "保存": 1.0, "导出": 1.0, "导入": 1.0, "上传": 1.0, "下载": 1.0,
        "选中": 1.0, "全选": 1.5, "光标": 1.0, "鼠标": 1.0, "键盘": 1.0,
        "重启": 1.0, "刷新": 1.0, "重置": 1.0, "卸载": 1.0,
        "文件夹": 1.0, "桌面": 0.5,
        "关闭": 0.5, "打开": 0.5, "红色": 0.5, "蓝色": 0.5, "绿色": 0.5,
        "上方": 0.3, "下方": 0.3, "旁边": 0.3,
        "clic": 1.0, "hacer clic": 2.0, "botón": 1.0, "ventana": 1.0, "pestaña": 1.0,
        "menú": 1.0, "barra": 2.0, "esquina": 2.0, "atajo": 2.0, "tecla": 1.0,
        "casilla": 1.0, "cuadro de texto": 2.0, "celda": 1.0, "configuración": 1.5
    ]

    public func observeSource(_ text: String) {
        applyHeuristic(from: text, weightMultiplier: 1.0)
    }

    public func observeTranslation(_ text: String) {
        applyHeuristic(from: text, weightMultiplier: 0.6)
    }

    private func applyHeuristic(from text: String, weightMultiplier: Double) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let haystack = trimmed.lowercased()
        var hitScore: Double = 0
        var hitKeywords: [String] = []

        for (keyword, weight) in Self.guiKeywordWeights {
            if haystack.contains(keyword.lowercased()) {
                hitScore += weight
                hitKeywords.append(keyword)
            }
        }

        sceneScore *= config.sceneScoreDecay
        sceneScore += hitScore * weightMultiplier
        sceneScore = min(sceneScore, config.sceneScoreCeiling)
        if sceneScore < 0.01 { sceneScore = 0 }

        let previousScene = currentScene
        switch currentScene {
        case .generalLifestyle:
            if sceneScore >= config.guiEnterThreshold {
                currentScene = .guiOperations
            }
        case .guiOperations:
            if sceneScore <= config.guiExitThreshold {
                currentScene = .generalLifestyle
            }
        }

        if ContextDebugFlags.logContextEngine {
            if previousScene != currentScene {
                print("🎭 [场景切换] \(previousScene.displayName) → \(currentScene.displayName) (score=\(String(format: "%.2f", sceneScore)))")
            } else if !hitKeywords.isEmpty {
                print("🔍 [场景信号] 命中 \(hitKeywords.prefix(5).joined(separator: "/")) +\(String(format: "%.1f", hitScore * weightMultiplier)) → score=\(String(format: "%.2f", sceneScore)) [\(currentScene.rawValue)]")
            }
        }
    }

    public func registerTermCorrection(original: String, corrected: String) {
        let cleanOriginal = original.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanCorrected = corrected.trimmingCharacters(in: .whitespacesAndNewlines)

        guard isAcceptableTerm(original: cleanOriginal, corrected: cleanCorrected) else {
            if ContextDebugFlags.logContextEngine {
                print("🚫 [术语拒收] \"\(cleanOriginal)\" → \"\(cleanCorrected)\"（未通过质量闸门）")
            }
            return
        }

        let key = normalizedKey(cleanOriginal)
        sequenceCounter += 1
        if var existing = glossary[key] {
            existing.corrected = cleanCorrected
            existing.sequence = sequenceCounter
            existing.hitCount += 1
            glossary[key] = existing
            if ContextDebugFlags.logContextEngine {
                print("📚 [术语更新] \(cleanOriginal) → \(cleanCorrected) (第 \(existing.hitCount) 次命中，共 \(glossary.count) 条)")
            }
        } else {
            glossary[key] = TermEntry(
                original: cleanOriginal,
                corrected: cleanCorrected,
                sequence: sequenceCounter,
                hitCount: 1
            )
            if ContextDebugFlags.logContextEngine {
                print("📚 [术语登记] \(cleanOriginal) → \(cleanCorrected)（共 \(glossary.count) 条）")
            }
        }
        evictIfNeeded()
    }

    private func isAcceptableTerm(original: String, corrected: String) -> Bool {
        guard !original.isEmpty, !corrected.isEmpty else { return false }
        guard normalizedKey(original) != normalizedKey(corrected) else { return false }
        guard original.count >= 2 else { return false }
        guard original.count <= 32, corrected.count <= 32 else { return false }
        guard !original.contains("\n"), !corrected.contains("\n") else { return false }
        return true
    }

    private func normalizedKey(_ text: String) -> String {
        text.lowercased()
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    private func evictIfNeeded() {
        guard glossary.count > config.maxGlossarySize else { return }
        let overflow = glossary.count - config.maxGlossarySize
        let victims = glossary
            .sorted { lhs, rhs in
                if lhs.value.hitCount != rhs.value.hitCount {
                    return lhs.value.hitCount < rhs.value.hitCount
                }
                return lhs.value.sequence < rhs.value.sequence
            }
            .prefix(overflow)
            .map(\.key)

        for key in victims {
            if ContextDebugFlags.logContextEngine, let entry = glossary[key] {
                print("🗑️ [术语淘汰] \(entry.original) → \(entry.corrected)（容量上限 \(config.maxGlossarySize)）")
            }
            glossary.removeValue(forKey: key)
        }
    }

    public func generateContextPrompt(for text: String) -> String {
        let effectiveScene = effectiveSceneConsideringCurrentSentence(text)
        var blocks: [String] = []

        if let sceneBlock = sceneInstructionBlock(for: effectiveScene) {
            blocks.append(sceneBlock)
        }
        if let glossaryBlock = glossaryPromptBlock() {
            blocks.append(glossaryBlock)
        }

        guard !blocks.isEmpty else { return "" }
        return "\n\n" + blocks.joined(separator: "\n\n")
    }

    private func effectiveSceneConsideringCurrentSentence(_ text: String) -> ConversationScene {
        if currentScene == .guiOperations { return .guiOperations }
        let haystack = text.lowercased()
        var instantScore: Double = 0
        for (keyword, weight) in Self.guiKeywordWeights {
            if haystack.contains(keyword.lowercased()) {
                instantScore += weight
                if instantScore >= config.guiEnterThreshold { break }
            }
        }
        return instantScore >= config.guiEnterThreshold ? .guiOperations : .generalLifestyle
    }

    private func sceneInstructionBlock(for scene: ConversationScene) -> String? {
        switch scene {
        case .generalLifestyle:
            return nil
        case .guiOperations:
            return """
            【当前场景】软件操作指导（远程协助 / 界面讲解）
            这一段对话正在讲解如何操作电脑软件，请按以下规则翻译：
            1. 界面元素名称（按钮、菜单、选项卡、对话框标题）必须精确直译，不要意译、不要改写。
            2. 快捷键与修饰键名保留英文原样，不要翻译也不要加注音：
               Command、Control、Option、Shift、Tab、Enter、Esc。
            3. 方位与顺序信息（左/右/上/下、第几行第几列、先…再…）必须完整保留，一个都不能省。
            4. 颜色、图标形状等视觉描述必须保留，它们是用户在屏幕上定位的唯一线索。
            5. 语气可以简洁直接（像技术支持在指导操作），不必刻意口语化或添加寒暄。
            """
        }
    }

    private func glossaryPromptBlock() -> String? {
        guard !glossary.isEmpty else { return nil }
        let selected = glossary.values
            .sorted { lhs, rhs in
                if lhs.hitCount != rhs.hitCount { return lhs.hitCount > rhs.hitCount }
                return lhs.sequence > rhs.sequence
            }
            .prefix(config.maxTermsInPrompt)

        guard !selected.isEmpty else { return nil }
        let lines = selected
            .map { "- \($0.original) -> \($0.corrected)" }
            .joined(separator: "\n")

        return """
        【术语与纠错对照】（请严格遵循以下纠错）：
        \(lines)
        """
    }

    public func snapshot() -> ContextSnapshot {
        let recent = glossary.values
            .sorted { $0.sequence > $1.sequence }
            .prefix(5)
            .map { (original: $0.original, corrected: $0.corrected) }
        return ContextSnapshot(
            scene: currentScene,
            sceneScore: sceneScore,
            glossaryCount: glossary.count,
            recentTerms: Array(recent)
        )
    }

    public func currentSceneMode() -> ConversationScene { currentScene }

    public func reset() {
        currentScene = .generalLifestyle
        sceneScore = 0
        glossary.removeAll()
        sequenceCounter = 0
        if ContextDebugFlags.logContextEngine {
            print("🧹 [上下文引擎] 已重置：场景回到默认，术语表清空（纯内存，无残留）")
        }
    }

    public func forceScene(_ scene: ConversationScene) {
        currentScene = scene
        switch scene {
        case .guiOperations:
            sceneScore = config.sceneScoreCeiling
        case .generalLifestyle:
            sceneScore = 0
        }
        if ContextDebugFlags.logContextEngine {
            print("🔧 [上下文引擎] 手动锁定场景 → \(scene.displayName)")
        }
    }
}

private enum ContextDebugFlags {
    static var logContextEngine: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}
