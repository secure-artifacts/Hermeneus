import SwiftUI
import AppKit
import Combine
import Foundation

// =========================================================
// 1. 独立微型状态机：完美绕过 @State 宏 Bug 与跨文件依赖
// =========================================================
public class CopyMenuState: ObservableObject {
    public static let shared = CopyMenuState()
    @Published public var isPresented: Bool = false
}

private struct SubtitleWindowAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { return FocusNSView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

private class FocusNSView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        DispatchQueue.main.async { [weak self] in
            guard let window = self?.window else { return }
            NSApp.setActivationPolicy(.regular)
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.isOpaque = false
            window.backgroundColor = .clear
            window.isMovableByWindowBackground = true
            window.level = .floating
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }
    override func mouseDown(with event: NSEvent) {
        super.mouseDown(with: event)
        NSApp.activate(ignoringOtherApps: true)
        self.window?.makeKeyAndOrderFront(nil)
    }
}

// =========================================================
// 2. 解耦的自定义彩色悬浮窗：彻底解决 Swift 编译超时，颜色精准对齐
// =========================================================
struct CustomCopyPopoverView: View {
    let onCopyBoth: () -> Void
    let onCopyRemote: () -> Void
    let onCopyLocal: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // 🟢 选项 1: 复制双方对话 (亮绿色)
            Button(action: onCopyBoth) {
                HStack {
                    Text("🎧🎙️ 复制双方对话")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(Color(red: 0.3, green: 0.95, blue: 0.45))
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Divider().background(Color.white.opacity(0.15))

            // 🔵 选项 2: 仅复制对方说 (浅蓝，与上方文字同色)
            Button(action: onCopyRemote) {
                HStack {
                    Text("🎧 仅复制“对方说”")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Divider().background(Color.white.opacity(0.15))

            // 🟡 选项 3: 仅复制我说 (亮黄，与下方文字同色)
            Button(action: onCopyLocal) {
                HStack {
                    Text("🎙️ 仅复制“我说”")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(.yellow)
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .frame(width: 175)
        .background(Color(white: 0.12)) // 独立深灰色背景，使色彩对比强烈
    }
}

// =========================================================
// 3. 主视图
// =========================================================
public struct SubtitleView: View {
    @ObservedObject var orchestrator: PipelineOrchestrator
    @ObservedObject var copyState = CopyMenuState.shared // 挂载独立状态机

    public init(orchestrator: PipelineOrchestrator) {
        self.orchestrator = orchestrator
    }

    private func copyToClipboard(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    private func copyBothDialogue() {
        var combined: [(speaker: String, source: String, trans: String, date: Date)] = []
        for turn in orchestrator.remoteBuffer.turns {
            combined.append(("[对方]", turn.sourceText, turn.translatedText, turn.timestamp))
        }
        for turn in orchestrator.localBuffer.turns {
            combined.append(("[我说]", turn.sourceText, turn.translatedText, turn.timestamp))
        }
        combined.sort { $0.date < $1.date }
        
        let text = combined.map { item in
            let transStr = item.trans.isEmpty ? "" : "\n(译：\(item.trans))"
            return "\(item.speaker) \(item.source)\(transStr)"
        }.joined(separator: "\n\n")
        copyToClipboard(text)
    }

    private func copyRemoteOnly() {
        let text = orchestrator.remoteBuffer.turns.map { turn in
            let transStr = turn.translatedText.isEmpty ? "" : "\n(译：\(turn.translatedText))"
            return "\(turn.sourceText)\(transStr)"
        }.joined(separator: "\n\n")
        copyToClipboard(text)
    }

    private func copyLocalOnly() {
        let text = orchestrator.localBuffer.turns.map { turn in
            let transStr = turn.translatedText.isEmpty ? "" : "\n(译：\(turn.translatedText))"
            return "\(turn.sourceText)\(transStr)"
        }.joined(separator: "\n\n")
        copyToClipboard(text)
    }

    // body 拆成几个小的 computed property，
    // 每一小块编译器都能很快独立类型推断出来，
    // 不再是一整棵几百行的表达式树。
    public var body: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: 0).background(SubtitleWindowAccessor())

            VStack(spacing: 0) {
                controlBar
                Divider().background(Color.white.opacity(0.15))
                subtitleArea
            }
            .background(Color.black.opacity(0.82))
            .cornerRadius(12)
        }
        .background(Color.clear)
    }

    // ================= 顶部控制栏 =================
    private var controlBar: some View {
        HStack(spacing: 10) {
            HeaderToggleChip(
                isOn: orchestrator.isListeningRemote,
                onIcon: "headphones",
                offIcon: "speaker.slash.fill",
                onText: "听对方",
                offText: "听对方 已关",
                onColor: .blue,
                action: { orchestrator.isListeningRemote.toggle() }
            )

            HeaderToggleChip(
                isOn: orchestrator.isSpeakingLocal,
                onIcon: "mic.fill",
                offIcon: "mic.slash.fill",
                onText: "我的麦克风",
                offText: "麦克风 已关",
                onColor: .red,
                action: { orchestrator.isSpeakingLocal.toggle() }
            )

            Spacer()

            copyButton
        }
        .padding(.horizontal, 10).padding(.top, 14).padding(.bottom, 8)
    }

    // 100% 还原原始的白底白字复制按钮
    private var copyButton: some View {
        Button(action: { copyState.isPresented.toggle() }) {
            HStack(spacing: 4) {
                Image(systemName: "doc.on.doc")
                Text("复制记录")
            }
            .font(.system(size: 11, weight: .bold))
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Color.white.opacity(0.15))
            .foregroundColor(.white)
            .cornerRadius(6)
        }
        .buttonStyle(.plain)
        // 唤起解耦的高性能彩色弹窗
        .popover(isPresented: $copyState.isPresented, arrowEdge: .bottom) {
            CustomCopyPopoverView(
                onCopyBoth: {
                    copyBothDialogue()
                    copyState.isPresented = false
                },
                onCopyRemote: {
                    copyRemoteOnly()
                    copyState.isPresented = false
                },
                onCopyLocal: {
                    copyLocalOnly()
                    copyState.isPresented = false
                }
            )
        }
    }

    // ================= 字幕区 =================
    private var subtitleArea: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                RemoteSubtitlePanel(orchestrator: orchestrator)
                    .frame(width: geometry.size.width, height: geometry.size.height * 0.5 - 0.5, alignment: .topLeading)
                    .background(Color.black.opacity(0.15))

                Divider().background(Color.white.opacity(0.2))

                LocalSubtitlePanel(orchestrator: orchestrator)
                    .frame(width: geometry.size.width, height: geometry.size.height * 0.5 - 0.5, alignment: .topLeading)
                    .background(Color.black.opacity(0.15))
            }
        }
    }
}

// =========================================================
// 4. 顶部开关小胶囊按钮：把"听对方"/"我的麦克风"两个结构相同的按钮
//    收敛成一个参数化组件，减少 body 里的重复表达式
// =========================================================
private struct HeaderToggleChip: View {
    let isOn: Bool
    let onIcon: String
    let offIcon: String
    let onText: String
    let offText: String
    let onColor: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: isOn ? onIcon : offIcon)
                    .font(.system(size: 11, weight: .bold))
                Text(isOn ? onText : offText)
                    .font(.system(size: 11, weight: .bold))
            }
            .foregroundColor(isOn ? .white : .white.opacity(0.5))
            .frame(width: 92, height: 24)
            .background(isOn ? onColor : Color.white.opacity(0.15))
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

// =========================================================
// 5. "对方说" 上半区：独立成一个 View，body 只需要处理自己这一块
// =========================================================
private struct RemoteSubtitlePanel: View {
    @ObservedObject var orchestrator: PipelineOrchestrator

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(orchestrator.remoteBuffer.turns) { turn in
                            RemoteTurnRow(turn: turn)
                        }
                        Color.clear.frame(height: 1).id("REMOTE_BOTTOM")
                    }
                    .padding(.horizontal, 12).padding(.top, 4)
                }
                .onChange(of: orchestrator.remoteBuffer.turns.count) { _, _ in
                    withAnimation { proxy.scrollTo("REMOTE_BOTTOM", anchor: .bottom) }
                }
                .onChange(of: orchestrator.remoteBuffer.turns.last?.translatedText) { _, _ in
                    withAnimation { proxy.scrollTo("REMOTE_BOTTOM", anchor: .bottom) }
                }
            }
        }
    }

    private var header: some View {
        HStack {
            HStack(spacing: 6) {
                Image(systemName: "headphones")
                Text("对方说 (系统音源)")
                    .font(.system(size: 13, weight: .bold))
            }
            .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
            Spacer()
        }
        .padding(.horizontal, 12).padding(.top, 8)
    }
}

private struct RemoteTurnRow: View {
    let turn: DialogueTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(turn.sourceText)
                .font(.system(size: 15))
                .foregroundColor(.white.opacity(0.85))
            Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                .font(.system(size: 17, weight: .bold))
                .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
        }
        .id(turn.id)
        .padding(.bottom, 4)
    }
}

// =========================================================
// 6. "我说" 下半区：同理独立成一个 View，
//    每一行再按"编辑中" / "展示中"两种状态拆成两个更小的 View
// =========================================================
private struct LocalSubtitlePanel: View {
    @ObservedObject var orchestrator: PipelineOrchestrator

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(orchestrator.localBuffer.turns) { turn in
                            if orchestrator.editingTurnId == turn.id {
                                LocalTurnEditRow(orchestrator: orchestrator, turn: turn)
                            } else {
                                LocalTurnDisplayRow(orchestrator: orchestrator, turn: turn)
                            }
                        }
                        Color.clear.frame(height: 1).id("LOCAL_BOTTOM")
                    }
                    .padding(.horizontal, 12).padding(.top, 4)
                }
                .onChange(of: orchestrator.localBuffer.turns.count) { _, _ in
                    withAnimation { proxy.scrollTo("LOCAL_BOTTOM", anchor: .bottom) }
                }
                .onChange(of: orchestrator.localBuffer.turns.last?.translatedText) { _, _ in
                    withAnimation { proxy.scrollTo("LOCAL_BOTTOM", anchor: .bottom) }
                }
            }
        }
    }

    private var header: some View {
        HStack {
            HStack(spacing: 6) {
                Image(systemName: "mic.fill")
                Text("我说 (麦克风 → BlackHole)")
                    .font(.system(size: 13, weight: .bold))
            }
            .foregroundColor(.yellow)
            Spacer()

            Button(action: { Task { await orchestrator.replayLastLocalTranslation() } }) {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                    Text("重播末句")
                }
                .font(.system(size: 11, weight: .bold)).foregroundColor(.white)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Color.blue.opacity(0.6)).cornerRadius(4)
            }.buttonStyle(.plain)
        }
        .padding(.horizontal, 12).padding(.top, 8)
    }
}

private struct LocalTurnEditRow: View {
    @ObservedObject var orchestrator: PipelineOrchestrator
    let turn: DialogueTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("中").font(.caption).foregroundColor(.gray)
                TextField("编辑中文...", text: $orchestrator.editingSourceText)
                    .textFieldStyle(.plain).foregroundColor(.white).padding(6)
                    .background(Color.white.opacity(0.15)).cornerRadius(4)
                    .onTapGesture {
                        NSApp.activate(ignoringOtherApps: true)
                    }
                Button("重译播报") {
                    let tid = turn.id
                    let txt = orchestrator.editingSourceText
                    orchestrator.editingTurnId = nil
                    Task { await orchestrator.retranslateAndSpeak(turnId: tid, newChineseText: txt) }
                }.buttonStyle(.borderedProminent).tint(.orange).controlSize(.small)
            }
            HStack {
                Text("西").font(.caption).foregroundColor(.gray)
                TextField("编辑西语...", text: $orchestrator.editingTransText)
                    .textFieldStyle(.plain).foregroundColor(.yellow).padding(6)
                    .background(Color.white.opacity(0.15)).cornerRadius(4)
                    .onTapGesture {
                        NSApp.activate(ignoringOtherApps: true)
                    }
                Button("仅重播") {
                    let tid = turn.id
                    let txt = orchestrator.editingTransText
                    orchestrator.editingTurnId = nil
                    Task { await orchestrator.updateSpanishAndSpeak(turnId: tid, newSpanishText: txt) }
                }.buttonStyle(.borderedProminent).tint(.blue).controlSize(.small)
            }
        }
        .id(turn.id)
        .padding(8).background(Color.black.opacity(0.5)).cornerRadius(8)
    }
}

private struct LocalTurnDisplayRow: View {
    @ObservedObject var orchestrator: PipelineOrchestrator
    let turn: DialogueTurn

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(turn.sourceText).font(.system(size: 15)).foregroundColor(.white.opacity(0.85))
                Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                    .font(.system(size: 17, weight: .bold)).foregroundColor(.yellow)
            }
            Spacer()

            HStack(spacing: 4) {
                Button(action: { Task { await orchestrator.speakSpanishText(turn.translatedText) } }) {
                    Image(systemName: "speaker.wave.2.fill")
                        .font(.system(size: 14)).foregroundColor(.white.opacity(0.6))
                        .frame(width: 32, height: 32).contentShape(Rectangle())
                }.buttonStyle(.plain)

                Button(action: {
                    orchestrator.editingTurnId = turn.id
                    orchestrator.editingSourceText = turn.sourceText
                    orchestrator.editingTransText = turn.translatedText
                    NSApp.activate(ignoringOtherApps: true)
                }) {
                    Image(systemName: "pencil")
                        .font(.system(size: 14)).foregroundColor(.white.opacity(0.6))
                        .frame(width: 32, height: 32).contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }
        .id(turn.id)
        .padding(.bottom, 4)
    }
}