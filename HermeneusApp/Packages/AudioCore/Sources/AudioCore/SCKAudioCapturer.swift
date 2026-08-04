import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia

public final class SCKAudioCapturer: NSObject, @unchecked Sendable, SCStreamOutput {
    public let ringBuffer = AudioRingBuffer(capacity: 48000 * 10)
    private var stream: SCStream?

    // ---------------------------------------------------------------
    // 生产者-消费者解耦：
    //   - didOutputSampleBuffer 在 SCKAudioQueue（串行 DispatchQueue）上被
    //     系统按音频到达的先后顺序依次调用——顺序天然是对的。
    //   - 但如果在这里对每一帧都开一个 Task { await ringBuffer.write(...) }，
    //     这些 Task 会被丢进 Swift 并发的全局线程池，不保证按创建顺序执行，
    //     负载高的时候后到的音频可能先写进 ringBuffer，导致音频错乱。
    //   - AsyncStream.Continuation.yield() 是同步、非阻塞、严格保序的，
    //     配合唯一一个常驻消费者 Task 顺序 await ringBuffer.write(...)，
    //     从架构上彻底消除乱序可能，也省掉了"每帧创建一个 Task"的调度开销。
    // ---------------------------------------------------------------
    private let sampleContinuation: AsyncStream<[Float]>.Continuation
    private let sampleStream: AsyncStream<[Float]>
    private var consumerTask: Task<Void, Never>?

    public override init() {
        var continuation: AsyncStream<[Float]>.Continuation!
        // .unbounded：消费者(ringBuffer.write，纯内存拷贝)理论上远快于
        // 音频产生速率，正常不会积压；如果你观察到极端情况下内存增长，
        // 可以换成 .bufferingNewest(N) 做丢帧保护（丢旧帧保最新）。
        self.sampleStream = AsyncStream<[Float]>(bufferingPolicy: .unbounded) { cont in
            continuation = cont
        }
        self.sampleContinuation = continuation
        super.init()

        let ringBuffer = self.ringBuffer
        let sampleStream = self.sampleStream
        consumerTask = Task {
            for await chunk in sampleStream {
                await ringBuffer.write(chunk)
            }
        }
    }

    deinit {
        sampleContinuation.finish()
        consumerTask?.cancel()
    }

    public func startCapture() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)

        let myPID = NSRunningApplication.current.processIdentifier
        guard let mainDisplay = content.displays.first else { return }

        // 排除本 App PID，仅采集外部应用（如 Zoom、浏览器）声音
        let excludedApps = content.applications.filter { $0.processID == myPID }
        let filter = SCContentFilter(display: mainDisplay, excludingApplications: excludedApps, exceptingWindows: [])

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true // 从 macOS 内核屏蔽本进程 TTS 播报音，彻底斩断死循环
        config.sampleRate = 48000
        config.channelCount = 1

        stream = SCStream(filter: filter, configuration: config, delegate: nil)
        try stream?.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "SCKAudioQueue"))
        try await stream?.startCapture()
        print("🎧 系统音频采集引擎（防自激隔离模式）已就绪！")
    }

    public func stopCapture() async throws {
        try await stream?.stopCapture()
        stream = nil
    }

    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        guard let pcmBuffer = extractPCMBuffer(from: sampleBuffer) else { return }
        guard let floatData = pcmBuffer.floatChannelData?[0] else { return }

        let frameLength = Int(pcmBuffer.frameLength)
        guard frameLength > 0 else { return }

        // 整块内存拷贝（memcpy），比逐元素 for 循环快，且从一开始就是 let，
        // 天然满足 Strict Concurrency 对并发捕获的要求，不需要额外快照变量。
        let samples = Array(UnsafeBufferPointer(start: floatData, count: frameLength))

        // yield 同步返回、极轻量（本质是把数据塞进队列），不阻塞音频回调线程，
        // 由唯一的消费者 Task 严格按到达顺序异步写入 ringBuffer。
        sampleContinuation.yield(samples)
    }

    private func extractPCMBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
        // 使用 Swift 原生 CoreMedia API 提取 Format 与 Data Buffer
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else {
            return nil
        }

        let format = AVAudioFormat(cmAudioFormatDescription: formatDescription)
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frameCount > 0 else { return nil }

        guard let pcmBuffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else {
            return nil
        }
        pcmBuffer.frameLength = AVAudioFrameCount(frameCount)

        let dataLength = CMBlockBufferGetDataLength(blockBuffer)
        guard let destinationPointer = pcmBuffer.mutableAudioBufferList.pointee.mBuffers.mData else {
            return nil
        }

        let status = CMBlockBufferCopyDataBytes(
            blockBuffer,
            atOffset: 0,
            dataLength: dataLength,
            destination: destinationPointer
        )

        return status == noErr ? pcmBuffer : nil
    }
}