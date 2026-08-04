import Foundation
import Combine
import AVFoundation
import AudioCore
import VADKit
import ASRKit
import TranslateKit
import TTSKit

public actor MicAudioBuffer {
    private var buffer: [Float] = []
    public init() {

}
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
    private var silenceMs: Double = 0
    private let sampleRate: Int
    private var detector: VoiceActivityDetecting

    private let maxBufferSeconds: Double
    private let minChunkSeconds: Double
    private let quickSilenceMs: Double
    private let conservativeSilenceMs: Double
    private let quickSilenceMinBufferSeconds: Double

    public init(
        sampleRate: Int,
        detector: VoiceActivityDetecting = AdaptiveEnergyDetector(),
        maxBufferSeconds: Double = 8.0, // 原 30.0 —— 硬上限，保证最坏情况下等待时间可预期，不然可以攒到30秒才出字
        minChunkSeconds: Double = 0.8,
        quickSilenceMs: Double = 700, // 原 1500 —— 已经攒了一段话时，停顿 0.7 秒就切，减少出字延迟
        conservativeSilenceMs: Double = 1200, // 原 2200 —— 刚开口时也不用等 2.2 秒才判断是不是换气
        quickSilenceMinBufferSeconds: Double = 1.2 // 原 2.5 —— 更早进入"快速切句"模式
    ) {
        self.sampleRate = sampleRate
        self.detector = detector
        self.maxBufferSeconds = maxBufferSeconds
        self.minChunkSeconds = minChunkSeconds
        self.quickSilenceMs = quickSilenceMs
        self.conservativeSilenceMs = conservativeSilenceMs
        self.quickSilenceMinBufferSeconds = quickSilenceMinBufferSeconds
    }

    public func feed(pcm: [Float]) -> [Float]? {
        guard !pcm.isEmpty else { return nil }
        let frameMs = Double(pcm.count) / Double(sampleRate) * 1000.0

        if detector.isSpeech(pcm: pcm) {
            silenceMs = 0
            buffer.append(contentsOf: pcm)

            if buffer.count >= Int(Double(sampleRate) * maxBufferSeconds) {
                return flushBuffer()
            }
        } else {
            guard !buffer.isEmpty else { return nil }
            silenceMs += frameMs
            buffer.append(contentsOf: pcm)

            let bufferedSeconds = Double(buffer.count) / Double(sampleRate)
            let requiredSilenceMs = bufferedSeconds >= quickSilenceMinBufferSeconds
                ? quickSilenceMs
                : conservativeSilenceMs

            if silenceMs >= requiredSilenceMs {
                silenceMs = 0
                if bufferedSeconds >= minChunkSeconds {
                    return flushBuffer()
                } else {
                    buffer.removeAll()
                }
            }
        }
        return nil
    }

    private func flushBuffer() -> [Float] {
        let chunk = buffer
        buffer.removeAll()
        silenceMs = 0
        return chunk
    }
}

public struct DialogueTurn: Identifiable, Equatable {
    public var timestamp: Date = Date()
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

    public func updateSourceText(turnId: UUID, newText: String) {
        if let idx = turns.firstIndex(where: { $0.id == turnId }) {
            turns[idx].sourceText = newText
        }
    }
    public func updateTranslatedText(turnId: UUID, newText: String) {
        if let idx = turns.firstIndex(where: { $0.id == turnId }) {
            turns[idx].translatedText = newText
        }
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

private actor SegmentProcessingQueue {
    private var pending: [Segment] = []
    private var isProcessing = false
    private let maxQueueDepth: Int
    private let handler: (Segment) async -> Void

    init(maxQueueDepth: Int = 2, handler: @escaping (Segment) async -> Void) {
        self.maxQueueDepth = maxQueueDepth
        self.handler = handler
    }

    func enqueue(_ segment: Segment) {
        pending.append(segment)
        if pending.count > maxQueueDepth {
            pending.removeFirst(pending.count - maxQueueDepth)
        }
        if !isProcessing {
            Task { await drain() }
        }
    }

    private func drain() async {
        guard !isProcessing else { return }
        isProcessing = true
        while !pending.isEmpty {
            let seg = pending.removeFirst()
            await handler(seg)
        }
        isProcessing = false
    }
}

@MainActor
public final class PipelineOrchestrator: ObservableObject {
    @Published public var isListeningRemote: Bool = true
    @Published public var isSpeakingLocal: Bool = true

    public let remoteBuffer = RollingTextBuffer(maxTurns: 9999)
    public let localBuffer = RollingTextBuffer(maxTurns: 9999)

    @Published public var isRunning: Bool = false
    @Published public var latencyMs: Int = 0

    @Published public var isTTSPlaying: Bool = false
    private var cancellables = Set<AnyCancellable>()

    @Published public var editingTurnId: UUID? = nil
    @Published public var editingSourceText: String = ""
    @Published public var editingTransText: String = ""


    private let systemCapturer = SCKAudioCapturer()
    private let micCapturer = MicrophoneCapturer()

    private let asr = WhisperASREngine()

    private let contextEngine = ConversationContextEngine()

    private lazy var translator = OllamaTranslationEngine(
        onTermCorrectionExtracted: { [contextEngine] original, corrected in
            Task {
                await contextEngine.registerTermCorrection(original: original, corrected: corrected)
            }
        },
        contextPromptProvider: { [contextEngine] text in
            await contextEngine.generateContextPrompt(for: text)
        }
    )

    private let tts = BlackHoleTTSEngine()
    private var scheduler: ClauseSpeechScheduler?

    private let micBufferActor = MicAudioBuffer()
    private let remoteSegmenter = ContinuousStreamSegmenter(sampleRate: 48000)
    private let localSegmenter = ContinuousStreamSegmenter(sampleRate: 44100)

    private lazy var remoteQueue = SegmentProcessingQueue(maxQueueDepth: 2) { [weak self] seg in
        await self?.processRemoteSegment(seg)
    }
    private lazy var localQueue = SegmentProcessingQueue(maxQueueDepth: 2) { [weak self] seg in
        await self?.processLocalSegment(seg)
    }

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
            print("🚀 V2.2 场景意识同传系统已就绪！")
        } catch {
            print("❌ 系统音频启动失败: \(error.localizedDescription)")
        }
    }

    public func stopSession() async {
        guard isRunning else { return }
        isRunning = false
        micCapturer.stopCapture()
        try? await systemCapturer.stopCapture()
        remoteBuffer.reset()
        localBuffer.reset()
        await contextEngine.reset()
        isTTSPlaying = false
        print("🛑 会话已结束，全部上下文已清空（纯内存，无残留）")
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
                        await self.remoteQueue.enqueue(seg)
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
                        await self.localQueue.enqueue(seg)
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

                await contextEngine.observeSource(text)

                let context = recentContext(from: remoteBuffer)

                let transStream = try await translator.translate(text: text, contextHistory: context, targetLanguage: "中文")
                for try await transUpdate in transStream {
                    switch transUpdate {
                    case .delta(let token):
                        remoteBuffer.appendTranslationDelta(turnId: turnId, token: token)
        DispatchQueue.main.async { self.objectWillChange.send() }
                    case .final(let fullTrans):
                        var finalText = fullTrans
                        if !finalText.isEmpty && !finalText.hasSuffix(" ") && !finalText.hasSuffix("。") {
                            finalText += "。"
                        }
                        remoteBuffer.finalizeTranslation(turnId: turnId, fullText: finalText)

                        Task { [contextEngine] in
                            await contextEngine.observeTranslation(finalText)
                        }
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

                await contextEngine.observeSource(text)

                let context = recentContext(from: localBuffer)

                let transStream = try await translator.translate(text: text, contextHistory: context, targetLanguage: "西班牙语")
                for try await transUpdate in transStream {
                    switch transUpdate {
                    case .delta(let token):
                        localBuffer.appendTranslationDelta(turnId: turnId, token: token)
        DispatchQueue.main.async { self.objectWillChange.send() }
                    case .clauseCommitted(_):
                        break // 移除流式重复播报，避免播放两次
                    case .final(let fullTrans):
                        localBuffer.finalizeTranslation(turnId: turnId, fullText: fullTrans)

                        Task { [contextEngine] in
                            await contextEngine.observeTranslation(fullTrans)
                        }

                        self.isTTSPlaying = true
                        try? await tts.speak(text: fullTrans, language: "es-ES")
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

    
    /// 增加轻量级末句重播接口
    
    /// 编辑中文原文后，触发重新翻译并向对方播报
    public func retranslateAndSpeak(turnId: UUID, newChineseText: String) async {
        localBuffer.updateSourceText(turnId: turnId, newText: newChineseText)
        DispatchQueue.main.async { self.objectWillChange.send() }
        localBuffer.updateTranslatedText(turnId: turnId, newText: "重新翻译中...")
        DispatchQueue.main.async { self.objectWillChange.send() }
        
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
        DispatchQueue.main.async { self.objectWillChange.send() }
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

    public func replayLastLocalTranslation() async {
        guard let lastTurn = localBuffer.turns.last, !lastTurn.translatedText.isEmpty else { return }
        DispatchQueue.main.async { self.objectWillChange.send() }
        self.isTTSPlaying = true
        try? await tts.speak(text: lastTurn.translatedText, language: "es-ES")
        try? await Task.sleep(nanoseconds: 300_000_000)
        self.isTTSPlaying = false
    }

    private func recentContext(from buffer: RollingTextBuffer) -> [TranslationContext] {
        let fifteenSecondsAgo = Date().addingTimeInterval(-15)
        let inWindowTurns = buffer.turns.filter { turn in
            turn.isTranslationFinal && !turn.translatedText.isEmpty && turn.timestamp >= fifteenSecondsAgo
        }
        let selectedTurns = inWindowTurns.isEmpty ? buffer.turns.filter({ $0.isTranslationFinal && !$0.translatedText.isEmpty }).suffix(4) : inWindowTurns.suffix(6)
        return selectedTurns.map { turn in
            TranslationContext(sourceText: turn.sourceText, translatedText: turn.translatedText)
        }
    }

    public func triggerUIRefresh() {
        DispatchQueue.main.async {
            self.objectWillChange.send()
        }
    }

}