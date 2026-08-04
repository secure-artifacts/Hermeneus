import os

sv_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/SubtitleView.swift")

pro_ui_code = r'''import SwiftUI
import AppKit

// MARK: - 底层视窗穿透 (消除标题栏，实现纯粹的透明玻璃窗口)
struct WindowAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            if let window = view.window {
                window.titleVisibility = .hidden
                window.titlebarAppearsTransparent = true
                window.isOpaque = false
                window.backgroundColor = .clear
                window.isMovableByWindowBackground = true // 允许拖拽透明背景移动窗口
            }
        }
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

public struct SubtitleView: View {
    @ObservedObject var orchestrator: PipelineOrchestrator

    public init(orchestrator: PipelineOrchestrator) {
        self.orchestrator = orchestrator
    }

    public var body: some View {
        VStack(spacing: 0) {
            // 挂载透明窗口 Hack
            Color.clear.frame(height: 0).background(WindowAccessor())

            // ================= 顶部控制栏 =================
            HStack(spacing: 12) {
                // 1. 听对方 (开启蓝色，关闭斜杠+灰色)
                Button(action: { orchestrator.isListeningRemote.toggle() }) {
                    HStack(spacing: 4) {
                        Image(systemName: orchestrator.isListeningRemote ? "headphones" : "speaker.slash.fill")
                        Text(orchestrator.isListeningRemote ? "听对方" : "听对方 /")
                            .strikethrough(!orchestrator.isListeningRemote)
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(orchestrator.isListeningRemote ? Color.blue : Color.gray.opacity(0.4))
                    .foregroundColor(orchestrator.isListeningRemote ? .white : .gray)
                    .cornerRadius(14)
                }
                .buttonStyle(.plain)

                // 2. 我的麦克风 (开启红色，关闭斜杠+灰色)
                Button(action: { orchestrator.isSpeakingLocal.toggle() }) {
                    HStack(spacing: 4) {
                        Image(systemName: orchestrator.isSpeakingLocal ? "mic.fill" : "mic.slash.fill")
                        Text(orchestrator.isSpeakingLocal ? "我的麦克风" : "麦克风 /")
                            .strikethrough(!orchestrator.isSpeakingLocal)
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(orchestrator.isSpeakingLocal ? Color.red : Color.gray.opacity(0.4))
                    .foregroundColor(orchestrator.isSpeakingLocal ? .white : .gray)
                    .cornerRadius(14)
                }
                .buttonStyle(.plain)

                Spacer()

                // 复制记录保留在右上角
                Button(action: {
                    let text = orchestrator.remoteBuffer.turns.map { "\($0.sourceText)\n\($0.translatedText)" }.joined(separator: "\n\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.on.doc")
                        Text("复制记录")
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Color.white.opacity(0.15))
                    .foregroundColor(.white)
                    .cornerRadius(6)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 12).padding(.top, 16).padding(.bottom, 8)

            Divider().background(Color.white.opacity(0.15))

            // ================= 上下分割字幕区 =================
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        
                        // -------- 上半部分：对方说 (青蓝色) --------
                        VStack(alignment: .leading, spacing: 10) {
                            HStack(spacing: 6) {
                                Image(systemName: "headphones")
                                Text("对方说 (系统音源)")
                                    .font(.caption.bold())
                            }
                            .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                            .padding(.bottom, 4)

                            ForEach(orchestrator.remoteBuffer.turns) { turn in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(turn.sourceText)
                                        .font(.system(size: 14))
                                        .foregroundColor(.white.opacity(0.8))
                                    Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                        .font(.system(size: 16, weight: .bold))
                                        .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                                }
                                .padding(.bottom, 8)
                            }
                        }

                        Divider().background(Color.white.opacity(0.15))

                        // -------- 下半部分：对他说 (黄色区域) --------
                        VStack(alignment: .leading, spacing: 10) {
                            // 黄色区域头部：标题 + 重播末句按钮
                            HStack {
                                HStack(spacing: 6) {
                                    Image(systemName: "mic.fill")
                                    Text("对他说 (麦克风 → BlackHole)")
                                        .font(.caption.bold())
                                }
                                .foregroundColor(.yellow)
                                
                                Spacer()
                                
                                // 重播末句按钮精准落户在黄色区域
                                Button(action: {
                                    Task { await orchestrator.replayLastLocalTranslation() }
                                }) {
                                    HStack(spacing: 4) {
                                        Image(systemName: "arrow.clockwise")
                                        Text("重播末句")
                                    }
                                    .font(.caption.bold())
                                    .foregroundColor(.white)
                                    .padding(.horizontal, 8).padding(.vertical, 4)
                                    .background(Color.blue.opacity(0.6))
                                    .cornerRadius(4)
                                }
                                .buttonStyle(.plain)
                            }
                            .padding(.bottom, 4)

                            // 麦克风记录与极简编辑框
                            ForEach(orchestrator.localBuffer.turns) { turn in
                                if orchestrator.editingTurnId == turn.id {
                                    // ✏️ 编辑模式
                                    VStack(alignment: .leading, spacing: 8) {
                                        // 编辑中文
                                        HStack {
                                            Text("中").font(.caption).foregroundColor(.gray)
                                            TextField("编辑中文...", text: $orchestrator.editingSourceText)
                                                .textFieldStyle(.plain)
                                                .foregroundColor(.white)
                                                .padding(6).background(Color.white.opacity(0.1)).cornerRadius(4)
                                            Button("重译播报") {
                                                let tid = turn.id; let txt = orchestrator.editingSourceText; orchestrator.editingTurnId = nil
                                                Task { await orchestrator.retranslateAndSpeak(turnId: tid, newChineseText: txt) }
                                            }
                                            .buttonStyle(.borderedProminent).tint(.orange).controlSize(.small)
                                        }
                                        // 编辑西语
                                        HStack {
                                            Text("西").font(.caption).foregroundColor(.gray)
                                            TextField("编辑西语...", text: $orchestrator.editingTransText)
                                                .textFieldStyle(.plain)
                                                .foregroundColor(.yellow)
                                                .padding(6).background(Color.white.opacity(0.1)).cornerRadius(4)
                                            Button("仅重播") {
                                                let tid = turn.id; let txt = orchestrator.editingTransText; orchestrator.editingTurnId = nil
                                                Task { await orchestrator.updateSpanishAndSpeak(turnId: tid, newSpanishText: txt) }
                                            }
                                            .buttonStyle(.borderedProminent).tint(.blue).controlSize(.small)
                                        }
                                    }
                                    .padding(8).background(Color.black.opacity(0.4)).cornerRadius(8)
                                } else {
                                    // 🟡 正常显示模式
                                    HStack(alignment: .top) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(turn.sourceText)
                                                .font(.system(size: 14))
                                                .foregroundColor(.white.opacity(0.8))
                                            Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                                .font(.system(size: 16, weight: .bold))
                                                .foregroundColor(.yellow)
                                        }
                                        
                                        Spacer()
                                        
                                        // 隐形操作图标 (喇叭 / 铅笔)
                                        HStack(spacing: 8) {
                                            Button(action: { Task { await orchestrator.speakSpanishText(turn.translatedText) } }) {
                                                Image(systemName: "speaker.wave.2.fill")
                                                    .foregroundColor(.white.opacity(0.4)).padding(4)
                                            }.buttonStyle(.plain)

                                            Button(action: {
                                                orchestrator.editingTurnId = turn.id
                                                orchestrator.editingSourceText = turn.sourceText
                                                orchestrator.editingTransText = turn.translatedText
                                            }) {
                                                Image(systemName: "pencil")
                                                    .foregroundColor(.white.opacity(0.4)).padding(4)
                                            }.buttonStyle(.plain)
                                        }
                                    }
                                    .padding(.bottom, 8)
                                }
                            }
                        }
                    }
                    .padding(16)
                }
            }
        }
        .background(Color.black.opacity(0.85)) // 全局暗色毛玻璃感
    }
}
'''

with open(sv_path, "w", encoding="utf-8") as f:
    f.write(pro_ui_code)

print("✅ 终极 UI 定制完成：透明标题栏、红色麦克风、斜杠关闭态、黄色区重播按钮、沉浸式编辑框！")
