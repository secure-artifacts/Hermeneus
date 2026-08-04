import AppKit
import Combine

/// 全局热键监听器：监听指定按键的按下/松开事件，用于驱动 PTT 模式。
/// 默认绑定右 Option 键（keyCode 61），因为它在正常打字时几乎不会被
/// 误触，且左右手都能单手操作，符合"边说话边按"的实际使用场景。
///
/// 实现方式：用 NSEvent 的全局 + 本地监听器双管齐下——
///   - addGlobalMonitorForEvents：App 不在前台/失焦时也能捕获按键
///   - addLocalMonitorForEvents：App 在前台时同样生效，且避免事件被
///     其他控件（如 TextField）截获后不再传播
///
/// 注意：全局监听器需要用户在系统设置里为本 App 授权"辅助功能"权限，
/// 否则 addGlobalMonitorForEvents 只能收到有限的事件类型。首次运行时
/// 应引导用户开启该权限（本类会在检测到无权限时打印提示，实际项目中
/// 建议在 UI 层弹出授权引导弹窗）。
@MainActor
public final class PTTHotkeyMonitor: ObservableObject {

    /// 目标键码，默认右 Option（Alt）。可通过 UI 提供的按键录制功能修改。
    @Published public var targetKeyCode: UInt16 = 61

    @Published public private(set) var isKeyCurrentlyDown: Bool = false

    public var onKeyDown: (() -> Void)?
    public var onKeyUp: (() -> Void)?

    private var globalMonitor: Any?
    private var localMonitor: Any?

    public init() {}

    public func startMonitoring() {
        stopMonitoring()

        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged, .keyDown, .keyUp]) { [weak self] event in
            self?.handleEvent(event)
        }

        localMonitor = NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown, .keyUp]) { [weak self] event in
            self?.handleEvent(event)
            return event
        }

        if !AXIsProcessTrusted() {
            print("⚠️ [PTT] 未授权辅助功能权限，全局热键在 App 失焦时可能无法响应。请前往系统设置 → 隐私与安全性 → 辅助功能，允许本 App。")
        }
    }

    public func stopMonitoring() {
        if let globalMonitor {
            NSEvent.removeMonitor(globalMonitor)
            self.globalMonitor = nil
        }
        if let localMonitor {
            NSEvent.removeMonitor(localMonitor)
            self.localMonitor = nil
        }
    }

    private func handleEvent(_ event: NSEvent) {
        // Option/Command/Shift/Control 这类修饰键在 macOS 上只会触发
        // .flagsChanged，不会有独立的 .keyDown/.keyUp，需要单独判断。
        if event.type == .flagsChanged, targetKeyCode == 58 || targetKeyCode == 61 {
            let isOptionPressed = event.modifierFlags.contains(.option)
            updateKeyState(isDown: isOptionPressed)
            return
        }

        guard event.keyCode == targetKeyCode else { return }

        switch event.type {
        case .keyDown:
            updateKeyState(isDown: true)
        case .keyUp:
            updateKeyState(isDown: false)
        default:
            break
        }
    }

    private func updateKeyState(isDown: Bool) {
        guard isDown != isKeyCurrentlyDown else { return }
        isKeyCurrentlyDown = isDown
        if isDown {
            onKeyDown?()
        } else {
            onKeyUp?()
        }
    }

    deinit {
        if let globalMonitor {
            NSEvent.removeMonitor(globalMonitor)
        }
        if let localMonitor {
            NSEvent.removeMonitor(localMonitor)
        }
    }
}