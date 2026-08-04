import Foundation
import Combine

// MARK: - OllamaModelManager
//
// 把“该用哪个 Ollama 模型”这件事从 OllamaTranslationEngine 里彻底剥离出来。
// 之前的问题：TranslateKit 里硬编码了 "qwen2.5:7b"，改模型 = 改代码 + 重新编译。
//
// 这个 actor 只做三件事：
//   1. 探测本地 Ollama 装了哪些模型 (GET /api/tags)
//   2. 按优先级 (默认 3b 优先，7b 兜底) + 用户历史选择，解析出"这次该用谁"
//   3. 持久化用户的手动选择 (UserDefaults)
//
// 它不知道 TranslationEngine 的存在，也不负责真正切换正在跑的引擎——
// 那是 OllamaModelStore（下面）和调用方 AppViewModel 的职责。这样
// TranslateKit 包可以完全不依赖 SwiftUI/Combine 就能编译。
public actor OllamaModelManager {

    public struct ModelInfo: Sendable, Equatable {
        public let name: String
    }

    private let tagsEndpoint: URL
    private let userDefaultsKey: String
    private let preferredOrder: [String]
    private let defaults: UserDefaults

    public init(
        baseURL: URL = URL(string: "http://localhost:11434")!,
        // 优先级列表：探测到就用；越靠前优先级越高。
        // 默认 3b 优先是因为同传场景要的是首字延迟，不是极限翻译质量。
        preferredOrder: [String] = ["qwen2.5:3b", "qwen2.5:7b"],
        userDefaultsKey: String = "Hermeneus.OllamaModelName",
        defaults: UserDefaults = .standard
    ) {
        self.tagsEndpoint = baseURL.appendingPathComponent("api/tags")
        self.preferredOrder = preferredOrder
        self.userDefaultsKey = userDefaultsKey
        self.defaults = defaults
    }

    /// 拉取本地已安装模型列表。网络失败 / Ollama 没启动时返回空数组，
    /// 而不是抛错——探测失败本身就应该走降级路径，不应该让调用方
    /// 再写一遍 try/catch 分支。
    public func fetchInstalledModels() async -> [ModelInfo] {
        do {
            var request = URLRequest(url: tagsEndpoint)
            request.timeoutInterval = 3.0
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                return []
            }
            guard
                let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                let models = json["models"] as? [[String: Any]]
            else {
                return []
            }
            return models.compactMap { entry in
                (entry["name"] as? String).map(ModelInfo.init)
            }
        } catch {
            return []
        }
    }

    /// 解析出这次应该使用的模型名，优先级：
    ///   1. 用户手动选过、且探测结果里确实还装着的模型
    ///      （探测失败即 installedNames 为空时，依然信任用户上次的选择，
    ///       不能因为 Ollama 一次没响应就把用户的手动配置覆盖掉）
    ///   2. preferredOrder 中第一个已安装的模型（默认命中 3b）
    ///   3. preferredOrder 的最后一项，作为静态兜底（即使探测完全失败也有值可返回）
    public func resolvePreferredModel() async -> String {
        let installed = await fetchInstalledModels()
        let installedNames = Set(installed.map(\.name))

        if let saved = defaults.string(forKey: userDefaultsKey),
           installedNames.isEmpty || installedNames.contains(saved) {
            return saved
        }

        for candidate in preferredOrder where installedNames.contains(candidate) {
            return candidate
        }

        return preferredOrder.last ?? "qwen2.5:7b"
    }

    /// 供设置界面调用：用户在 HUD / 设置里手动切换模型时持久化。
    public func selectModel(_ name: String) {
        defaults.set(name, forKey: userDefaultsKey)
    }
}

// MARK: - OllamaModelStore
//
// 挂给 SwiftUI 用的 ObservableObject 外壳。真正的探测/持久化逻辑都在
// 上面的 actor 里；这一层只负责把 async actor 方法转成 @Published 状态,
// 以及在用户切换时通过 onModelChanged 回调通知正在运行的翻译引擎。
@MainActor
public final class OllamaModelStore: ObservableObject {

    @Published public private(set) var availableModels: [String] = []
    @Published public private(set) var isProbing: Bool = false

    @Published public var selectedModel: String = "" {
        didSet {
            guard selectedModel != oldValue, !selectedModel.isEmpty else { return }
            let manager = self.manager
            let model = selectedModel
            Task {
                await manager.selectModel(model)
                onModelChanged?(model)
            }
        }
    }

    /// 由外部（AppViewModel）注入：每次 selectedModel 变化时，把新模型名
    /// 同步给正在跑的 OllamaTranslationEngine.updateModel(_:)。
    public var onModelChanged: ((String) -> Void)?

    private let manager: OllamaModelManager

    public init(manager: OllamaModelManager = OllamaModelManager()) {
        self.manager = manager
    }

    /// App 启动时 / 设置页出现时调用一次：拉取本地模型列表，
    /// 解析出应使用的模型，并广播给引擎。
    public func refresh() async {
        isProbing = true
        defer { isProbing = false }

        let installed = await manager.fetchInstalledModels()
        availableModels = installed.map(\.name)

        let resolved = await manager.resolvePreferredModel()
        // 直接赋值会触发上面的 didSet -> selectModel + onModelChanged，
        // 所以启动时只需要调用一次 refresh() 就能把引擎也对齐好。
        if resolved != selectedModel {
            selectedModel = resolved
        } else {
            onModelChanged?(resolved)
        }
    }
}
