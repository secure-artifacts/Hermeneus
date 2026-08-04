import os

sv_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/SubtitleView.swift")

perfect_ui_code = r'''import SwiftUI

public struct SubtitleView: View {
    @ObservedObject var orchestrator: PipelineOrchestrator

    public init(orchestrator: PipelineOrchestrator) {
        self.orchestrator = orchestrator
    }

    public var body: some View {
        VStack(spacing: 0) {
            // ================= 顶部控制栏 =================
            HStack(spacing: 12) {
                // 经典胶囊开关
                HStack(spacing: 8) {
                    Toggle(isOn: $orchestrator.isListeningRemote) {
                        Label("听对方", systemImage: "headphones")
                    }
                    .toggleStyle(.button)
                    .tint(.blue)

                    Toggle(isOn: $orchestrator.isSpeakingLocal) {
                        Label("我的麦克风", systemImage: "mic.fill")
                    }
                    .toggleStyle(.button)
                    .tint(.blue)
                }
                
                Spacer()
                
                // 物理重播按钮（融入暗黑风格）
                Button(action: {
                    Task { await orchestrator.replayLastLocalTranslation() }
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.clockwise")
                        Text("重播末句")
                    }
                    .font(.caption.bold())
                    .foregroundColor(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.blue.opacity(0.7))
                    .cornerRadius(6)
                }
                .buttonStyle(.plain)

                // 复制记录
                Button(action: {
                    let text = orchestrator.remoteBuffer.turns.map { "\($0.sourceText)\n\($0.translatedText)" }.joined(separator: "\n\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                }) {
                    Label("复制记录", systemImage: "doc.on.doc")
                        .font(.caption.bold())
                        .foregroundColor(.white)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Color.white.opacity(0.15))
                        .cornerRadius(6)
                }
                .buttonStyle(.plain)
            }
            .padding(10)
            .background(Color.black.opacity(0.5))

            // ================= 字幕滚动区 =================
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        
                        // 1. 对方说区域 (还原暗青色区块)
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(spacing: 6) {
                                Image(systemName: "headphones")
                                Text("对方说 (系统音源)")
                                    .font(.system(size: 13, weight: .bold))
                            }
                            .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                            .padding(.bottom, 2)

                            ForEach(orchestrator.remoteBuffer.turns) { turn in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(turn.sourceText)
                                        .font(.system(size: 14))
                                        .foregroundColor(.white.opacity(0.9))
                                    Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                        .font(.system(size: 15, weight: .bold))
                                        .foregroundColor(Color(red: 0.2, green: 0.8, blue: 1.0))
                                }
                                .padding(.bottom, 6)
                            }
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(red: 0.02, green: 0.15, blue: 0.2)) // 经典暗青色背景
                        .cornerRadius(12)

                        // 2. 麦克风区域 (还原暗黄色区块)
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(spacing: 6) {
                                Image(systemName: "mic.fill")
                                Text("对他说 (麦克风 → BlackHole → Zoom)")
                                    .font(.system(size: 13, weight: .bold))
                            }
                            .foregroundColor(.yellow)
                            .padding(.bottom, 2)

                            ForEach(orchestrator.localBuffer.turns) { turn in
                                if orchestrator.editingTurnId == turn.id {
                                    // 🟢 编辑模式 (沉浸式暗黑输入框)
                                    VStack(alignment: .leading, spacing: 10) {
                                        HStack {
                                            Text("中:").foregroundColor(.white.opacity(0.7)).font(.caption)
                                            TextField("修改中文", text: $orchestrator.editingSourceText)
                                                .textFieldStyle(.roundedBorder)
                                                .colorScheme(.dark)
                                            Button("重译播报") {
                                                let tid = turn.id; let txt = orchestrator.editingSourceText; orchestrator.editingTurnId = nil
                                                Task { await orchestrator.retranslateAndSpeak(turnId: tid, newChineseText: txt) }
                                            }
                                            .buttonStyle(.borderedProminent)
                                            .tint(.orange)
                                            .controlSize(.small)
                                        }
                                        HStack {
                                            Text("西:").foregroundColor(.white.opacity(0.7)).font(.caption)
                                            TextField("修改西语", text: $orchestrator.editingTransText)
                                                .textFieldStyle(.roundedBorder)
                                                .colorScheme(.dark)
                                            Button("仅重播") {
                                                let tid = turn.id; let txt = orchestrator.editingTransText; orchestrator.editingTurnId = nil
                                                Task { await orchestrator.updateSpanishAndSpeak(turnId: tid, newSpanishText: txt) }
                                            }
                                            .buttonStyle(.borderedProminent)
                                            .tint(.blue)
                                            .controlSize(.small)
                                        }
                                    }
                                    .padding(10)
                                    .background(Color.black.opacity(0.4))
                                    .cornerRadius(8)
                                } else {
                                    // 🟡 正常展示模式
                                    HStack(alignment: .top) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(turn.sourceText)
                                                .font(.system(size: 14))
                                                .foregroundColor(.white.opacity(0.9))
                                            Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                                .font(.system(size: 15, weight: .bold))
                                                .foregroundColor(.yellow)
                                        }
                                        
                                        Spacer()
                                        
                                        // 优雅隐藏的单句播放与编辑按钮
                                        HStack(spacing: 12) {
                                            Button(action: { 
                                                Task { await orchestrator.speakSpanishText(turn.translatedText) } 
                                            }) {
                                                Image(systemName: "speaker.wave.2.fill")
                                                    .foregroundColor(.white.opacity(0.7))
                                                    .frame(width: 26, height: 26)
                                                    .background(Color.black.opacity(0.3))
                                                    .clipShape(Circle())
                                            }
                                            .buttonStyle(.plain)

                                            Button(action: {
                                                orchestrator.editingTurnId = turn.id
                                                orchestrator.editingSourceText = turn.sourceText
                                                orchestrator.editingTransText = turn.translatedText
                                            }) {
                                                Image(systemName: "pencil")
                                                    .foregroundColor(.white.opacity(0.7))
                                                    .frame(width: 26, height: 26)
                                                    .background(Color.black.opacity(0.3))
                                                    .clipShape(Circle())
                                            }
                                            .buttonStyle(.plain)
                                        }
                                    }
                                    .padding(.bottom, 6)
                                }
                            }
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(red: 0.2, green: 0.15, blue: 0.0)) // 经典暗黄色背景
                        .cornerRadius(12)
                    }
                    .padding(12)
                }
            }
        }
        .background(Color.black.opacity(0.85)) // 底部玻璃黑
    }
}
'''

with open(sv_path, "w", encoding="utf-8") as f:
    f.write(perfect_ui_code)

print("✅ UI 已完美复原为你最喜欢的双色区块样式，同时优雅融入了编辑与播放按钮！")
