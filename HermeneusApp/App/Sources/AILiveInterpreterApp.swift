import SwiftUI
import AppKit

@main
struct AILiveInterpreterApp: App {
    @StateObject private var orchestrator = PipelineOrchestrator()

    var body: some Scene {
        WindowGroup {
            SubtitleView(orchestrator: orchestrator)
                .background(WindowAccessor())
                .onAppear {
                    Task {
                        await orchestrator.startSession()
                    }
                }
        }
        .windowResizability(.contentMinSize)
        .windowStyle(.hiddenTitleBar)
    }
}

struct WindowAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            if let window = view.window {
                window.isOpaque = false
                window.backgroundColor = .clear
                window.hasShadow = true
                
                // 1. 解禁原生 macOS 窗口的 8 方向 (上下左右 + 四个角) 拉伸权限
                window.styleMask = [.titled, .resizable, .closable, .miniaturizable, .fullSizeContentView]
                window.titlebarAppearsTransparent = true
                window.titleVisibility = .hidden
                
                // 2. 点击顶部标题栏或任意背景区域即可直接拖拽移动窗口
                window.isMovableByWindowBackground = true
                
                // 3. 确保交互响应正常
                window.ignoresMouseEvents = false
                window.level = .floating
                window.showsResizeIndicator = true
            }
        }
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
