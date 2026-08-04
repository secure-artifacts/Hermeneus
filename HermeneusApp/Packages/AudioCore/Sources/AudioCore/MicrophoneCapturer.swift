import Foundation
import AVFoundation

public final class MicrophoneCapturer: @unchecked Sendable {
    private let audioEngine = AVAudioEngine()
    private var isCapturing = false
    public var onAudioChunk: ((AudioSlice) -> Void)?
    
    public init() {
        // 监听系统音频路由变更通知（防止 TTS 播报打断麦克风）
        NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: audioEngine,
            queue: .main
        ) { [weak self] _ in
            guard let self = self, self.isCapturing else { return }
            print("🔄 检测到系统音频路由变更，正在自动重连麦克风...")
            try? self.restartEngine()
        }
    }
    
    public func startCapture() throws {
        guard !isCapturing else { return }
        try setupAndStart()
    }
    
    private func setupAndStart() throws {
        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 2400, format: format) { [weak self] buffer, _ in
            guard let self = self, let channelData = buffer.floatChannelData?[0] else { return }
            let frameLength = Int(buffer.frameLength)
            var pcm = [Float](repeating: 0, count: frameLength)
            for i in 0..<frameLength { pcm[i] = channelData[i] }
            
            let slice = AudioSlice(pcmData: pcm, sampleRate: Int(format.sampleRate))
            self.onAudioChunk?(slice)
        }
        
        audioEngine.prepare()
        try audioEngine.start()
        isCapturing = true
        print("🎙️ 麦克风硬件引擎配置就绪且已激活！")
    }
    
    private func restartEngine() throws {
        audioEngine.stop()
        try setupAndStart()
    }
    
    public func stopCapture() {
        if isCapturing {
            audioEngine.inputNode.removeTap(onBus: 0)
            audioEngine.stop()
            isCapturing = false
        }
    }
}
