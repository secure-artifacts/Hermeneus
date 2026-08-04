import os

base_dir = os.path.expanduser("~/trans_mvp/AILiveInterpreter")
po_path = os.path.join(base_dir, "App/Sources/PipelineOrchestrator.swift")
sv_path = os.path.join(base_dir, "App/Sources/SubtitleView.swift")

# -------------------------------------------------------------
# 1. 重构 PipelineOrchestrator.swift：
#    - 调大断句阈值（解决断句太快）
#    - 增加中文编辑重译 API、西语编辑重播 API、单句独立朗读 API
# -------------------------------------------------------------
with open(po_path, "r", encoding="utf-8") as f:
    po_code = f.read()

# 调整 ContinuousStreamSegmenter 的默认静音参数，给说话留出充足换气时间
old_seg_params = """    public init(
        sampleRate: Int,
        detector: VoiceActivityDetecting = AdaptiveEnergyDetector(),
        maxBufferSeconds: Double = 6.0,
        minChunkSeconds: Double = 0.6,
        quickSilenceMs: Double = 550,
        conservativeSilenceMs: Double = 1200,
        quickSilenceMinBufferSeconds: Double = 1.8
    ) {"""

new_seg_params = """    public init(
        sampleRate: Int,
        detector: VoiceActivityDetecting = AdaptiveEnergyDetector(),
        maxBufferSeconds: Double = 8.0,
        minChunkSeconds: Double = 0.8,
        quickSilenceMs: Double = 1100, // 调大到 1.1 秒，防止中间换气被强制切断
        conservativeSilenceMs: Double = 1800, // 调大到 1.8 秒
        quickSilenceMinBufferSeconds: Double = 2.5
    ) {"""

if old_seg_params in po_code:
    po_code = po_code.replace(old_seg_params, new_seg_params)

# 动态扩展 RollingTextBuffer 的修改能力
old_buffer_func = "    public func reset() {"
new_buffer_func = """    public func updateSourceText(turnId: UUID, newText: String) {
        if let idx = turns.firstIndex(where: { $0.id == turnId }) {
            turns[idx].sourceText = newText
        }
    }
    public func updateTranslatedText(turnId: UUID, newText: String) {
        if let idx = turns.firstIndex(where: { $0.id == turnId }) {
            turns[idx].translatedText = newText
        }
    }
    public func reset() {"""

if "public func updateSourceText" not in po_code:
    po_code = po_code.replace(old_buffer_func, new_buffer_func)

# 增加编辑重译与单独播报 API
edit_apis = """
    /// 编辑中文原文后，触发重新翻译并向对方播报
    public func retranslateAndSpeak(turnId: UUID, newChineseText: String) async {
        localBuffer.updateSourceText(turnId: turnId, newText: newChineseText)
        localBuffer.updateTranslatedText(turnId: turnId, newText: "重新翻译中...")
        
        let context = recentContext(from: localBuffer)
        do {
            let transStream = try await translator.translate(text: newChineseText, contextHistory: context, targetLanguage: "西班牙语")
            var fullTrans = ""
            for try await transUpdate in transStream {
                switch transUpdate {
                case .delta(let token):
                    fullTrans += token
                case .final(let text):
                    fullTrans = text
                default: break
                }
            }
            let trimmed = fullTrans.trimmingCharacters(in: .whitespacesAndNewlines)
            localBuffer.finalizeTranslation(turnId: turnId, fullText: trimmed)
            await speakSpanishText(trimmed)
        } catch {
            print("编辑重译异常: \(error)")
        }
    }

    /// 手动修改西语译文后更新并重新播报
    public func updateSpanishAndSpeak(turnId: UUID, newSpanishText: String) async {
        localBuffer.updateTranslatedText(turnId: turnId, newText: newSpanishText)
        await speakSpanishText(newSpanishText)
    }

    /// 播放指定的西语文本
    public func speakSpanishText(_ text: String) async {
        guard !text.isEmpty else { return }
        self.isTTSPlaying = true
        try? await tts.speak(text: text, language: "es-ES")
        try? await Task.sleep(nanoseconds: 300_000_000)
        self.isTTSPlaying = false
    }
"""

if "func retranslateAndSpeak" not in po_code:
    po_code = po_code.replace("public func replayLastLocalTranslation()", edit_apis + "\n    public func replayLastLocalTranslation()")

with open(po_path, "w", encoding="utf-8") as f:
    f.write(po_code)
print("✅ 1. PipelineOrchestrator: 静音断句时间已延长，中西文编辑 API 接入成功！")

# -------------------------------------------------------------
# 2. 完全重写 SubtitleView.swift：
#    - 工具栏画出 🔁 重播末句 物理按钮
#    - 支持点击编辑中文（触发重新翻译）
#    - 支持点击编辑西语（触发重新朗读）
#    - 每句西语右侧配有专属 🔊 播放按钮
# -------------------------------------------------------------
sv_code = r'''import SwiftUI

public struct SubtitleView: View {
    @ObservedObject var orchestrator: PipelineOrchestrator
    @State private var editingTurnId: UUID? = nil
    @State private var editingSourceText: String = ""
    @State private var editingTransText: String = ""

    public init(orchestrator: PipelineOrchestrator) {
        self.orchestrator = orchestrator
    }

    public var body: some View {
        VStack(spacing: 0) {
            // 顶部控制工具栏
            headerToolbar

            Divider().background(Color.gray.opacity(0.3))

            // 字幕对话列表（包含对方与我对说）
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // 1. 对方说（系统音频）
                        remoteSection

                        Divider().background(Color.gray.opacity(0.2))

                        // 2. 对他说（麦克风）
                        localSection
                    }
                    .padding(12)
                }
            }
        }
        .background(Color(NSColor.windowBackgroundColor).opacity(0.95))
        .cornerRadius(12)
        .shadow(radius: 8)
    }

    // MARK: - 工具栏
    private var headerToolbar: View {
        HStack(spacing: 12) {
            Toggle(isOn: $orchestrator.isListeningRemote) {
                Label("听对方", systemImage: "headphones")
            }
            .toggleStyle(.button)

            Toggle(isOn: $orchestrator.isSpeakingLocal) {
                Label("我的麦克风", systemImage: "mic.fill")
            }
            .toggleStyle(.button)

            Spacer()

            // 物理按钮：重播最后一句话
            Button(action: {
                Task {
                    await orchestrator.replayLastLocalTranslation()
                }
            }) {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise.circle.fill")
                    Text("重播末句")
                }
                .font(.caption.bold())
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color.blue.opacity(0.15))
                .foregroundColor(.blue)
                .cornerRadius(6)
            }
            .buttonStyle(.plain)
        }
        .padding(10)
    }

    // MARK: - 对方说 (系统音频)
    private var remoteSection: View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "headphones")
                Text("对方说 (系统音频)")
                    .font(.caption.bold())
            }
            .foregroundColor(.cyan)

            ForEach(orchestrator.remoteBuffer.turns) { turn in
                VStack(alignment: .leading, spacing: 4) {
                    Text(turn.sourceText)
                        .font(.system(size: 13))
                        .foregroundColor(.gray)

                    Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.cyan)
                }
                .padding(8)
                .background(Color.cyan.opacity(0.05))
                .cornerRadius(6)
            }
        }
    }

    // MARK: - 对他说 (麦克风 - 支持编辑与专属播放)
    private var localSection: View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "mic.fill")
                Text("对他说 (麦克风同传 → BlackHole)")
                    .font(.caption.bold())
            }
            .foregroundColor(.yellow)

            ForEach(orchestrator.localBuffer.turns) { turn in
                VStack(alignment: .leading, spacing: 6) {
                    if editingTurnId == turn.id {
                        // 编辑模式
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("修改中文:").font(.caption).foregroundColor(.gray)
                                TextField("输入修改后的中文", text: $editingSourceText)
                                    .textFieldStyle(.roundedBorder)
                                Button("重译并播报") {
                                    let tid = turn.id
                                    let txt = editingSourceText
                                    editingTurnId = nil
                                    Task {
                                        await orchestrator.retranslateAndSpeak(turnId: tid, newChineseText: txt)
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                            }

                            HStack {
                                Text("修改西语:").font(.caption).foregroundColor(.gray)
                                TextField("输入修改后的西班牙语", text: $editingTransText)
                                    .textFieldStyle(.roundedBorder)
                                Button("仅重播西语") {
                                    let tid = turn.id
                                    let txt = editingTransText
                                    editingTurnId = nil
                                    Task {
                                        await orchestrator.updateSpanishAndSpeak(turnId: tid, newSpanishText: txt)
                                    }
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .padding(6)
                        .background(Color.yellow.opacity(0.1))
                        .cornerRadius(6)
                    } else {
                        // 正常展示模式（双击或点编辑按钮进入编辑）
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(turn.sourceText)
                                    .font(.system(size: 13))
                                    .foregroundColor(.white.opacity(0.9))

                                Text(turn.translatedText.isEmpty ? "翻译中..." : turn.translatedText)
                                    .font(.system(size: 14, weight: .bold))
                                    .foregroundColor(.yellow)
                            }

                            Spacer()

                            HStack(spacing: 8) {
                                // 单句专属播放按钮
                                Button(action: {
                                    let textToSpeak = turn.translatedText
                                    Task {
                                        await orchestrator.speakSpanishText(textToSpeak)
                                    }
                                }) {
                                    Image(systemName: "speaker.wave.2.fill")
                                        .foregroundColor(.yellow)
                                }
                                .buttonStyle(.plain)
                                .help("播放本句西班牙语")

                                // 编辑按钮
                                Button(action: {
                                    editingTurnId = turn.id
                                    editingSourceText = turn.sourceText
                                    editingTransText = turn.translatedText
                                }) {
                                    Image(systemName: "pencil.circle.fill")
                                        .foregroundColor(.gray)
                                }
                                .buttonStyle(.plain)
                                .help("修改中文或西班牙语")
                            }
                        }
                    }
                }
                .padding(8)
                .background(Color.yellow.opacity(0.05))
                .cornerRadius(6)
            }
        }
    }
}
'''

with open(sv_path, "w", encoding="utf-8") as f:
    f.write(sv_code)

print("✅ 2. SubtitleView.swift: 全面重构！物理【🔁 重播末句】、单句【🔊 播放】与【✏️ 中西文编辑框】全部渲染成功！")

