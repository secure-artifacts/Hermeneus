import os

sv_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/SubtitleView.swift")

perfect_code = r'''import SwiftUI
import AppKit

// 透明窗口控制 (避免类名冲突，使用私有结构)
private struct SubtitleWindowAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            if let window = view.window {
                window.titleVisibility = .hidden
                window.titlebarAppearsTransparent = true
                window.isOpaque = false
                window.backgroundColor = .clear
                window.isMovableByWindowBackground = true
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
            // 挂载透明窗口穿透
            Color.clear.frame(height: 0).background(SubtitleWindowAccessor())

            // ================= 1. 顶部控制栏 (纯透明玻璃) =================
            HStack(spacing: 12) {
                // 听对方
                Button(action: { orchestrator.isListeningRemote.toggle() }) {
                    HStack(spacing: 4) {
                        Image(systemName: orchestrator.isListeningRemote ? "headphones" : "speaker.slash.fill")
                        Text(orchestrator.isListeningRemote ? "听对方" : "听对方 已关闭")
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(orchestrator.isListeningRemote ? Color.blue : Color.gray.opacity(0.3))
                    .foregroundColor(orchestrator.isListeningRemote ? .white : .gray)
                    .cornerRadius(14)
                }
                .buttonStyle(.plain)

                // 我的麦克风
                Button(action: { orchestrator.isSpeakingLocal.toggle() }) {
                    HStack(spacing: 4) {
                        Image(systemName: orchestrator.isSpeakingLocal ? "mic.fill" : "mic.slash.fill")
                        Text(orchestrator.isSpeakingLocal ? "我的麦克风" : "麦克风 已关闭")
                    }
                    .font(.caption.bold())
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(orchestrator.isSpeakingLocal ? Color.red : Color.gray.opacity(0.3))
                    .foregroundColor(orchestrator.isSpeakingLocal ? .white : .gray)
                    .cornerRadius(14)
                }
                .buttonStyle(.plain)

                Spacer()

                // 复制记录
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
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(Color.white.opacity(0.15))
                    .foregroundColor(.white)
                    .cornerRadius(6)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 12)
            .padding(.top, 16)
            .padding(.bottom, 8)
            .background(Color.clear) // 纯透明玻璃

            Divider().background(Color.white.opacity(0.15))

            // ================= 2. 50/50 上下对半分字幕区 =================
            GeometryReader { geometry in
                VStack(spacing: 0) {
                    
                    // -------- 上半部分：对方说 (50% 高度 - 深青色) --------
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 6) {
                            Image(systemName: "headphones")
                            Text("对方说 (系统音源)")
                                .font(.system(size: 14, weight: .bold))
                        }
                        .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                        .padding(.horizontal, 12)
                        .padding(.top, 10)

                        ScrollView {
                            VStack(alignment: .leading, spacing: 12) {
                                ForEach(orchestrator.remoteBuffer.turns) { turn in
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(turn.sourceText)
                                            .font(.system(size: 15)) // 放大字号
                                            .foregroundColor(.white.opacity(0.85))
                                        Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                            .font(.system(size: 17, weight: .bold)) // 放大字号
                                            .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                                    }
                                    .padding(.bottom, 4)
                                }
                            }
                            .padding(.horizontal, 12)
                        }
                    }
                    .frame(height: geometry.size.height * 0.5 - 0.5)
                    .background(Color(red: 0.02, green: 0.12, blue: 0.18).opacity(0.85))

                    Divider().background(Color.white.opacity(0.2))

                    // -------- 下半部分：我说 (50% 高度 - 深黄色) --------
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            HStack(spacing: 6) {
                                Image(systemName: "mic.fill")
                                Text("我说 (麦克风 → BlackHole)")
                                    .font(.system(size: 14, weight: .bold))
                            }
                            .foregroundColor(.yellow)

                            Spacer()

                            // 重播末句精准落在黄色区域右上角
                            Button(action: {
                                Task { await orchestrator.replayLastLocalTranslation() }
                            }) {
                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.clockwise")
                                    Text("重播末句")
                                }
                                .font(.caption.bold())
                                .foregroundColor(.white)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color.blue.opacity(0.6))
                                .cornerRadius(4)
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, 10)

                        ScrollView {
                            VStack(alignment: .leading, spacing: 12) {
                                ForEach(orchestrator.localBuffer.turns) { turn in
                                    if orchestrator.editingTurnId == turn.id {
                                        // 编辑模式
                                        VStack(alignment: .leading, spacing: 8) {
                                            HStack {
                                                Text("中").font(.caption).foregroundColor(.gray)
                                                TextField("编辑中文...", text: $orchestrator.editingSourceText)
                                                    .textFieldStyle(.plain)
                                                    .foregroundColor(.white)
                                                    .padding(6)
                                                    .background(Color.white.opacity(0.12))
                                                    .cornerRadius(4)
                                                Button("重译播报") {
                                                    let tid = turn.id; let txt = orchestrator.editingSourceText; orchestrator.editingTurnId = nil
                                                    Task { await orchestrator.retranslateAndSpeak(turnId: tid, newChineseText: txt) }
                                                }
                                                .buttonStyle(.borderedProminent).tint(.orange).controlSize(.small)
                                            }
                                            HStack {
                                                Text("西").font(.caption).foregroundColor(.gray)
                                                TextField("编辑西语...", text: $orchestrator.editingTransText)
                                                    .textFieldStyle(.plain)
                                                    .foregroundColor(.yellow)
                                                    .padding(6)
                                                    .background(Color.white.opacity(0.12))
                                                    .cornerRadius(4)
                                                Button("仅重播") {
                                                    let tid = turn.id; let txt = orchestrator.editingTransText; orchestrator.editingTurnId = nil
                                                    Task { await orchestrator.updateSpanishAndSpeak(turnId: tid, newSpanishText: txt) }
                                                }
                                                .buttonStyle(.borderedProminent).tint(.blue).controlSize(.small)
                                            }
                                        }
                                        .padding(8)
                                        .background(Color.black.opacity(0.4))
                                        .cornerRadius(8)
                                    } else {
                                        // 正常展示模式
                                        HStack(alignment: .top) {
                                            VStack(alignment: .leading, spacing: 4) {
                                                Text(turn.sourceText)
                                                    .font(.system(size: 15)) // 放大字号
                                                    .foregroundColor(.white.opacity(0.85))
                                                Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                                    .font(.system(size: 17, weight: .bold)) // 放大字号
                                                    .foregroundColor(.yellow)
                                            }

                                            Spacer()

                                            HStack(spacing: 8) {
                                                Button(action: { Task { await orchestrator.speakSpanishText(turn.translatedText) } }) {
                                                    Image(systemName: "speaker.wave.2.fill")
                                                        .foregroundColor(.white.opacity(0.5)).padding(4)
                                                }.buttonStyle(.plain)

                                                Button(action: {
                                                    orchestrator.editingTurnId = turn.id
                                                    orchestrator.editingSourceText = turn.sourceText
                                                    orchestrator.editingTransText = turn.translatedText
                                                }) {
                                                    Image(systemName: "pencil")
                                                        .foregroundColor(.white.opacity(0.5)).padding(4)
                                                }.buttonStyle(.plain)
                                            }
                                        }
                                        .padding(.bottom, 4)
                                    }
                                }
                            }
                            .padding(.horizontal, 12)
                        }
                    }
                    .frame(height: geometry.size.height * 0.5 - 0.5)
                    .background(Color(red: 0.18, green: 0.14, blue: 0.02).opacity(0.85))
                }
            }
        }
        .background(Color.black.opacity(0.85))
    }
}
'''

with open(sv_path, "w", encoding="utf-8") as f:
    f.write(perfect_code)

print("✅ SubtitleView.swift 重构成功！50/50对半分、玻璃透明顶部、斜杠关闭状态与大字体全面生效！")
