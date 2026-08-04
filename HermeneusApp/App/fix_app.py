import os

# ==========================================
# 1. 修复 PipelineOrchestrator.swift (动态防死循环)
# ==========================================
code_po = r"""import Foundation
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
            print("🚀 极速防自激同传系统已就绪！")
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
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self = self else { break }
                guard await self.isListeningRemote else { continue }

                if await self.isTTSPlaying { continue }

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
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self = self else { break }
                guard await self.isSpeakingLocal else { continue }

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

                        // 激活防回流屏蔽锁
                        self.isTTSPlaying = true
                        try? await tts.speak(text: fullTrans, language: "es-ES")
                        
                        // 【核心修复：动态计算延时】基础 1.5 秒 + 每字符 0.08 秒
                        let baseTime: Double = 1.5
                        let charTime: Double = Double(fullTrans.count) * 0.08
                        let totalSleepSeconds = baseTime + charTime
                        let sleepNanos = UInt64(totalSleepSeconds * 1_000_000_000)
                        
                        try? await Task.sleep(nanoseconds: sleepNanos)
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
path_po = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/PipelineOrchestrator.swift")
with open(path_po, "w", encoding="utf-8") as f:
    f.write(code_po)

# ==============================================================
# 2. 修复 AILiveInterpreterApp.swift (彻底解禁窗口拉伸与按钮点击限制)
# ==============================================================
code_app = r"""import SwiftUI
import AppKit

@main
struct AILiveInterpreterApp: App {
    @StateObject private var orchestrator = PipelineOrchestrator()

    var body: some Scene {
        WindowGroup {
            SubtitleView(orchestrator: orchestrator)
                // 绑定并注入底层原生窗口属性
                .background(WindowAccessor())
                .onAppear {
                    Task {
                        await orchestrator.startSession()
                    }
                }
        }
        // SwiftUI 层级允许窗口自由伸缩
        .windowResizability(.contentMinSize)
        .windowStyle(.hiddenTitleBar)
    }
}

// 核心修复：修改 macOS 原生窗口属性，允许拉伸、拖拽、鼠标点击按钮
struct WindowAccessor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            if let window = view.window {
                // 窗口透明
                window.isOpaque = false
                window.backgroundColor = .clear
                window.hasShadow = false
                
                // 1. 系统底层允许拉伸缩放
                window.styleMask.insert(.resizable)
                
                // 2. 取消鼠标穿透（让所有的 SwiftUI 按钮和 Toggle 恢复可点击）
                window.ignoresMouseEvents = false
                
                // 3. 允许按住任何非按钮区域直接拖动窗口
                window.isMovableByWindowBackground = true
                
                // 4. 始终悬浮在最前
                window.level = .floating
            }
        }
        return view
    }
    func updateNSView(_ nsView: NSView, context: Context) {}
}
"""
path_app = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/AILiveInterpreterApp.swift")
with open(path_app, "w", encoding="utf-8") as f:
    f.write(code_app)

print("✅ 第一步：防自激死循环已修复完毕！")
print("✅ 第二步：UI 窗口点击、拖拽、拉伸功能已解锁完毕！")
