import os

base_dir = os.path.expanduser("~/trans_mvp/AILiveInterpreter")

# 1. 重构 TTS 引擎 (接入 Delegate 精确监听播放结束)
tts_code = r"""import Foundation
import AVFoundation
import CoreAudio

@MainActor
public final class BlackHoleTTSEngine: NSObject, @unchecked Sendable, TTSEngine, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var blackHoleDeviceID: AudioDeviceID?
    private var continuation: CheckedContinuation<Void, Never>?

    public override init() {
        super.init()
        synthesizer.delegate = self
        findBlackHoleDevice()
    }

    private func findBlackHoleDevice() {
        var propertySize: UInt32 = 0
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize) == noErr else { return }
        let deviceCount = Int(propertySize) / MemoryLayout<AudioDeviceID>.size
        var deviceIDs = [AudioDeviceID](repeating: 0, count: deviceCount)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize, &deviceIDs) == noErr else { return }

        for id in deviceIDs {
            var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            var nameAddress = AudioObjectPropertyAddress(
                mSelector: kAudioObjectPropertyName,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var unmanagedName: Unmanaged<CFString>? = nil
            if AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &unmanagedName) == noErr, let cfName = unmanagedName?.takeRetainedValue() {
                let nameStr = cfName as String
                if nameStr.contains("BlackHole") {
                    self.blackHoleDeviceID = id
                    print("🔊 已成功关联 BlackHole 虚拟音频设备 ID: \(id)")
                    break
                }
            }
        }
    }

    public func speak(text: String, language: String = "es-ES") async throws {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate

        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            self.continuation = cont
            self.synthesizer.speak(utterance)
        }
    }

    public func stopPlayout() async {
        synthesizer.stopSpeaking(at: .immediate)
        if let cont = continuation {
            continuation = nil
            cont.resume()
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let cont = self.continuation {
                self.continuation = nil
                cont.resume()
            }
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let cont = self.continuation {
                self.continuation = nil
                cont.resume()
            }
        }
    }
}
"""
with open(os.path.join(base_dir, "Packages/TTSKit/Sources/TTSKit/BlackHoleTTSEngine.swift"), "w", encoding="utf-8") as f:
    f.write(tts_code)

# 2. 重构 PipelineOrchestrator.swift (屏蔽期间物理倾倒 Buffer 清空废音频)
po_code = r"""import Foundation
import Combine
import AVFoundation
import AudioCore
import VADKit
import ASRKit
import TranslateKit
import TTSKit

public actor MicAudioBuffer {
    private var buffer: [Float] = []
    public init() {}
    public func append(pcm: [Float]) { buffer.append(contentsOf: pcm) }
    public func flush() -> [Float] {
        let current = buffer
        buffer.removeAll()
        return current
    }
}

public protocol VoiceActivityDetecting {
    mutating func isSpeech(pcm: [Float]) -> Bool
}

public struct AdaptiveEnergyDetector: VoiceActivityDetecting {
    private var noiseFloor: Float
    private let noiseFloorAdaptRate: Float
    private let enterMultiplier: Float
    private let holdMultiplier: Float
    private let minEnterThreshold: Float
    private let minHoldThreshold: Float
    private var wasSpeech = false

    public init(
        initialNoiseFloor: Float = 0.0015,
        noiseFloorAdaptRate: Float = 0.05,
        enterMultiplier: Float = 2.2,
        holdMultiplier: Float = 1.4,
        minEnterThreshold: Float = 0.0025,
        minHoldThreshold: Float = 0.0015
    ) {
        self.noiseFloor = initialNoiseFloor
        self.noiseFloorAdaptRate = noiseFloorAdaptRate
        self.enterMultiplier = enterMultiplier
        self.holdMultiplier = holdMultiplier
        self.minEnterThreshold = minEnterThreshold
        self.minHoldThreshold = minHoldThreshold
    }

    public mutating func isSpeech(pcm: [Float]) -> Bool {
        guard !pcm.isEmpty else { return false }
        let sumSq = pcm.reduce(Float(0)) { $0 + $1 * $1 }
        let rms = sqrt(sumSq / Float(pcm.count))

        let enterThreshold = max(noiseFloor * enterMultiplier, minEnterThreshold)
        let holdThreshold = max(noiseFloor * holdMultiplier, minHoldThreshold)

        let speech = wasSpeech ? (rms > holdThreshold) : (rms > enterThreshold)

        if !speech {
            noiseFloor = noiseFloor * (1 - noiseFloorAdaptRate) + rms * noiseFloorAdaptRate
        }

        wasSpeech = speech
        return speech
    }
}

public actor ContinuousStreamSegmenter {
    private var buffer: [Float] = []
    private var silenceFrames: Int = 0
    private let sampleRate: Int
    private var detector: VoiceActivityDetecting
    private let silenceFramesToFinalize: Int

    public init(
        sampleRate: Int,
        detector: VoiceActivityDetecting = AdaptiveEnergyDetector(),
        silenceFramesToFinalize: Int = 5
    ) {
        self.sampleRate = sampleRate
        self.detector = detector
        self.silenceFramesToFinalize = silenceFramesToFinalize
    }

    public func feed(pcm: [Float]) -> [Float]? {
        guard !pcm.isEmpty else { return nil }

        if detector.isSpeech(pcm: pcm) {
            silenceFrames = 0
            buffer.append(contentsOf: pcm)

            if buffer.count >= Int(Double(sampleRate) * 2.2) {
                let chunk = buffer
                buffer.removeAll()
                return chunk
            }
        } else {
            if !buffer.isEmpty {
                silenceFrames += 1
                buffer.append(contentsOf: pcm)

                if silenceFrames >= silenceFramesToFinalize {
                    let chunk = buffer
                    buffer.removeAll()
                    silenceFrames = 0
                    if chunk.count >= Int(Double(sampleRate) * 0.7) {
                        return chunk
                    }
                }
            }
        }
        return nil
    }
}

public struct DialogueTurn: Identifiable, Equatable {
    public let id = UUID()
    public var sourceText: String
    public var translatedText: String
    public var isTranslationFinal: Bool = false
}

@MainActor
public final class RollingTextBuffer: ObservableObject {
    @Published public private(set) var turns: [DialogueTurn] = []
    private let maxTurns: Int

    public init(maxTurns: Int = 3) {
        self.maxTurns = maxTurns
    }

    @discardableResult
    public func beginTurn(sourceText: String) -> UUID? {
        if let last = turns.last, Self.isNearDuplicate(last.sourceText, sourceText) {
            return nil
        }
        let turn = DialogueTurn(sourceText: sourceText, translatedText: "")
        turns.append(turn)
        trimIfNeeded()
        return turn.id
    }

    public func appendTranslationDelta(turnId: UUID, token: String) {
        guard let idx = turns.firstIndex(where: { $0.id == turnId }) else { return }
        turns[idx].translatedText += token
    }

    public func finalizeTranslation(turnId: UUID, fullText: String? = nil) {
        guard let idx = turns.firstIndex(where: { $0.id == turnId }) else { return }
        if let fullText, !fullText.isEmpty {
            turns[idx].translatedText = fullText
        }
        turns[idx].isTranslationFinal = true
    }

    public func reset() {
        turns.removeAll()
    }

    public var joinedSourceText: String {
        turns.map(\.sourceText).joined(separator: "，")
    }
    public var joinedTranslatedText: String {
        turns.map(\.translatedText).joined(separator: " ")
    }

    private func trimIfNeeded() {
        if turns.count > maxTurns {
            turns.removeFirst(turns.count - maxTurns)
        }
    }

    private static func isNearDuplicate(_ a: String, _ b: String) -> Bool {
        guard !a.isEmpty, !b.isEmpty else { return false }
        if a == b { return true }
        let shorter = a.count <= b.count ? a : b
        let longer = a.count <= b.count ? b : a
        guard !shorter.isEmpty else { return false }
        return longer.contains(shorter) && Double(shorter.count) / Double(longer.count) > 0.85
    }
}

@MainActor
public final class PipelineOrchestrator: ObservableObject {
    @Published public var isListeningRemote: Bool = true
    @Published public var isSpeakingLocal: Bool = true

    public let remoteBuffer = RollingTextBuffer(maxTurns: 3)
    public let localBuffer = RollingTextBuffer(maxTurns: 3)

    @Published public var isRunning: Bool = false
    @Published public var latencyMs: Int = 0

    @Published public var isTTSPlaying: Bool = false

    private let systemCapturer = SCKAudioCapturer()
    private let micCapturer = MicrophoneCapturer()

    private let asr = WhisperASREngine()
    private let translator = OllamaTranslationEngine()
    private let tts = BlackHoleTTSEngine()
    private var scheduler: ClauseSpeechScheduler?

    private let micBufferActor = MicAudioBuffer()
    private let remoteSegmenter = ContinuousStreamSegmenter(sampleRate: 48000)
    private let localSegmenter = ContinuousStreamSegmenter(sampleRate: 44100)

    public init() {
        let ttsEngine = self.tts
        self.scheduler = ClauseSpeechScheduler(ttsEngine: ttsEngine)
    }

    public func startSession() async {
        guard !isRunning else { return }
        isRunning = true

        AVCaptureDevice.requestAccess(for: .audio) { granted in
            if granted {
                Task { @MainActor in
                    do {
                        self.setupMicCallback()
                        try self.micCapturer.startCapture()
                    } catch {
                        print("麦克风启动失败: \(error)")
                    }
                }
            }
        }

        do {
            try await systemCapturer.startCapture()
            startSystemAudioLoop()
            startMicAudioLoop()
            print("🚀 绝对隔离防自激同传系统已就绪！")
        } catch {
            print("❌ 系统音频启动失败: \(error.localizedDescription)")
        }
    }

    private func setupMicCallback() {
        let bufferActor = self.micBufferActor
        micCapturer.onAudioChunk = { slice in
            Task {
                await bufferActor.append(pcm: slice.pcmData)
            }
        }
    }

    private func startSystemAudioLoop() {
        let segmenter = self.remoteSegmenter
        Task.detached(priority: .userInitiated) { [weak self] in
            var seq: UInt64 = 0
            while await self?.isRunning == true {
                try? await Task.sleep(nanoseconds: 80_000_000)
                guard let self = self else { break }

                let listening = await self.isListeningRemote
                let ttsPlaying = await self.isTTSPlaying

                // 关闭状态或 TTS 播报期间：直接排出并丢弃积压采样，防止解锁后二次触发！
                if !listening || ttsPlaying {
                    _ = await self.systemCapturer.ringBuffer.readLatest(count: 9600)
                    continue
                }

                let samples = await self.systemCapturer.ringBuffer.readLatest(count: 4800)
                guard !samples.isEmpty else { continue }

                if let speechPCM = await segmenter.feed(pcm: samples) {
                    seq += 1
                    let slice = AudioSlice(pcmData: speechPCM, sampleRate: 48000)
                    let seg = Segment(id: SegmentID(channel: .remote, seq: seq), audio: slice)

                    Task { @MainActor in
                        await self.processRemoteSegment(seg)
                    }
                }
            }
        }
    }

    private func startMicAudioLoop() {
        let bufferActor = self.micBufferActor
        let segmenter = self.localSegmenter
        Task.detached(priority: .userInitiated) { [weak self] in
            var seq: UInt64 = 0
            while await self?.isRunning == true {
                try? await Task.sleep(nanoseconds: 80_000_000)
                guard let self = self else { break }

                let speaking = await self.isSpeakingLocal
                let ttsPlaying = await self.isTTSPlaying

                // 关闭状态或 TTS 播报期间：直接冲刷清空麦克风 Buffer！
                if !speaking || ttsPlaying {
                    _ = await self.micBufferActor.flush()
                    continue
                }

                let pcmChunk = await bufferActor.flush()
                if let speechPCM = await segmenter.feed(pcm: pcmChunk) {
                    seq += 1
                    let slice = AudioSlice(pcmData: speechPCM, sampleRate: 44100)
                    let seg = Segment(id: SegmentID(channel: .local, seq: seq), audio: slice)

                    Task { @MainActor in
                        await self.processLocalSegment(seg)
                    }
                }
            }
        }
    }

    private func processRemoteSegment(_ seg: Segment) async {
        do {
            let stream = try await asr.transcribe(segment: seg, language: "es")
            for try await update in stream {
                guard case .final(let text, _) = update, !text.isEmpty else { continue }
                guard let turnId = remoteBuffer.beginTurn(sourceText: text) else { continue }

                let transStream = try await translator.translate(text: text, contextHistory: [], targetLanguage: "中文")
                for try await transUpdate in transStream {
                    switch transUpdate {
                    case .delta(let token):
                        remoteBuffer.appendTranslationDelta(turnId: turnId, token: token)
                    case .final(let fullTrans):
                        var finalText = fullTrans
                        if !finalText.isEmpty && !finalText.hasSuffix(" ") && !finalText.hasSuffix("。") {
                            finalText += "。"
                        }
                        remoteBuffer.finalizeTranslation(turnId: turnId, fullText: finalText)
                    default: break
                    }
                }
            }
        } catch {
            print("远端处理异常: \(error)")
        }
    }

    private func processLocalSegment(_ seg: Segment) async {
        do {
            let stream = try await asr.transcribe(segment: seg, language: "zh")
            for try await update in stream {
                guard case .final(let text, _) = update, !text.isEmpty else { continue }
                guard let turnId = localBuffer.beginTurn(sourceText: text) else { continue }

                let transStream = try await translator.translate(text: text, contextHistory: [], targetLanguage: "西班牙语")
                for try await transUpdate in transStream {
                    switch transUpdate {
                    case .delta(let token):
                        localBuffer.appendTranslationDelta(turnId: turnId, token: token)
                    case .clauseCommitted(let clause):
                        await scheduler?.enqueueClause(text: clause, language: "es-ES")
                    case .final(let fullTrans):
                        localBuffer.finalizeTranslation(turnId: turnId, fullText: fullTrans)

                        // 激活硬件级隔离防回流锁
                        self.isTTSPlaying = true
                        
                        // 等待 TTSDelegate 异步播报完毕
                        try? await tts.speak(text: fullTrans, language: "es-ES")
                        
                        // 预留 0.3 秒余音衰减
                        try? await Task.sleep(nanoseconds: 300_000_000)
                        self.isTTSPlaying = false
                    }
                }
            }
        } catch {
            print("麦克风同传异常: \(error)")
            self.isTTSPlaying = false
        }
    }
}
"""
with open(os.path.join(base_dir, "App/Sources/PipelineOrchestrator.swift"), "w", encoding="utf-8") as f:
    f.write(po_code)

# 3. 重构 SubtitleView.swift (带有划线图标按钮 + 原版全屏拉伸)
sv_code = r"""import SwiftUI
import AppKit

public struct SubtitleView: View {
    @ObservedObject var orchestrator: PipelineOrchestrator
    @ObservedObject var remoteBuffer: RollingTextBuffer
    @ObservedObject var localBuffer: RollingTextBuffer

    public init(orchestrator: PipelineOrchestrator) {
        self.orchestrator = orchestrator
        self.remoteBuffer = orchestrator.remoteBuffer
        self.localBuffer = orchestrator.localBuffer
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {

            // 1. 顶部控制栏 (带划线图标)
            HStack(spacing: 12) {
                // 按钮 1：听对方
                Button(action: {
                    orchestrator.isListeningRemote.toggle()
                }) {
                    HStack(spacing: 5) {
                        Image(systemName: orchestrator.isListeningRemote ? "headphones" : "headphones.slash")
                            .font(.system(size: 12, weight: .bold))
                        Text(orchestrator.isListeningRemote ? "听对方" : "听对方 (已关)")
                            .font(.system(size: 11, weight: .bold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(orchestrator.isListeningRemote ? Color.white : Color.gray.opacity(0.3))
                    .foregroundColor(orchestrator.isListeningRemote ? .black : .white.opacity(0.6))
                    .cornerRadius(6)
                }
                .buttonStyle(PlainButtonStyle())

                // 按钮 2：我的麦克风
                Button(action: {
                    orchestrator.isSpeakingLocal.toggle()
                }) {
                    HStack(spacing: 5) {
                        Image(systemName: orchestrator.isSpeakingLocal ? "mic.fill" : "mic.slash.fill")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundColor(orchestrator.isSpeakingLocal ? .red : .white.opacity(0.6))
                        Text(orchestrator.isSpeakingLocal ? "我的麦克风" : "麦克风 (已关)")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(orchestrator.isSpeakingLocal ? .red : .white.opacity(0.6))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(orchestrator.isSpeakingLocal ? Color.white : Color.gray.opacity(0.3))
                    .cornerRadius(6)
                }
                .buttonStyle(PlainButtonStyle())

                Spacer()

                // 一键复制
                Button(action: {
                    let allText = "【对方说】\n\(remoteBuffer.joinedSourceText)\n\(remoteBuffer.joinedTranslatedText)\n\n【对他说】\n\(localBuffer.joinedSourceText)\n\(localBuffer.joinedTranslatedText)"
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(allText, forType: .string)
                }) {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.on.doc")
                        Text("复制记录")
                            .font(.system(size: 11, weight: .bold))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color.white)
                    .foregroundColor(.black)
                    .cornerRadius(6)
                }
                .buttonStyle(PlainButtonStyle())
            }

            Divider().background(Color.white.opacity(0.2))

            // 2. 对方说 (系统音源)
            DialogueChannelView(
                icon: "🎧",
                label: "对方说 (系统音源)",
                accentColor: .cyan,
                turns: remoteBuffer.turns,
                emptySourcePlaceholder: "等待对方讲话...",
                emptyTranslationPlaceholder: "译文 (ZH)",
                scrollAnchorId: "remoteBottom"
            )
            .padding(10)
            .background(Color.cyan.opacity(0.12))
            .cornerRadius(8)

            // 3. 对他说 (麦克风)
            DialogueChannelView(
                icon: "🎙️",
                label: "对他说 (麦克风 ➔ BlackHole ➔ Zoom)",
                accentColor: .yellow,
                turns: localBuffer.turns,
                emptySourcePlaceholder: "开启麦克风讲话...",
                emptyTranslationPlaceholder: "译文 (ES) 自动播报中",
                scrollAnchorId: "localBottom"
            )
            .padding(10)
            .background(Color.yellow.opacity(0.12))
            .cornerRadius(8)
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(Color.black.opacity(0.92))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .stroke(Color.white.opacity(0.25), lineWidth: 1)
                )
        )
        .shadow(color: Color.black.opacity(0.6), radius: 15, x: 0, y: 5)
        .frame(minWidth: 450, idealWidth: 550, maxWidth: .infinity, minHeight: 250, idealHeight: 360, maxHeight: .infinity)
    }
}

private struct DialogueChannelView: View {
    let icon: String
    let label: String
    let accentColor: Color
    let turns: [DialogueTurn]
    let emptySourcePlaceholder: String
    let emptyTranslationPlaceholder: String
    let scrollAnchorId: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("\(icon) \(label)")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(accentColor)
                Spacer()
            }

            ScrollViewReader { proxy in
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(alignment: .leading, spacing: 10) {
                        if turns.isEmpty {
                            Text(emptySourcePlaceholder)
                                .font(.system(size: 13))
                                .foregroundColor(.white.opacity(0.85))

                            Text(emptyTranslationPlaceholder)
                                .font(.system(size: 14, weight: .bold))
                                .foregroundColor(accentColor)
                        } else {
                            ForEach(turns) { turn in
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(turn.sourceText)
                                        .font(.system(size: 13))
                                        .foregroundColor(.white.opacity(0.85))
                                        .textSelection(.enabled)
                                        .fixedSize(horizontal: false, vertical: true)

                                    Text(turn.translatedText)
                                        .font(.system(size: 14, weight: .bold))
                                        .foregroundColor(accentColor)
                                        .opacity(turn.isTranslationFinal ? 1.0 : 0.6)
                                        .textSelection(.enabled)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        }

                        Color.clear.frame(height: 1).id(scrollAnchorId)
                    }
                }
                .onChange(of: turns) { _ in
                    withAnimation { proxy.scrollTo(scrollAnchorId, anchor: .bottom) }
                }
            }
            .frame(maxHeight: .infinity)
        }
    }
}
"""
with open(os.path.join(base_dir, "App/Sources/SubtitleView.swift"), "w", encoding="utf-8") as f:
    f.write(sv_code)

# 4. 重构 AILiveInterpreterApp.swift (开放原生 8 方向拉伸 + 顶栏/背景移动)
app_code = r"""import SwiftUI
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
"""
with open(os.path.join(base_dir, "App/Sources/AILiveInterpreterApp.swift"), "w", encoding="utf-8") as f:
    f.write(app_code)

print("🎉 自动化优化已全部覆盖更新完成！")
