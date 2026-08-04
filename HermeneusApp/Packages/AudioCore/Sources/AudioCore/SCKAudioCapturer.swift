import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia

public final class SCKAudioCapturer: NSObject, @unchecked Sendable, SCStreamOutput {
    public let ringBuffer = AudioRingBuffer(capacity: 48000 * 10)
    private var stream: SCStream?
    
    public override init() {
        super.init()
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
        let samples = [Float](repeating: 0, count: frameLength)
        for i in 0..<frameLength {
            samples[i] = floatData[i]
        }
        
        Task {
            await ringBuffer.write(samples)
        }
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
