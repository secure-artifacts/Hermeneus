import Foundation
import AudioCore

public actor EnergyVADEngine: VADEngine {
    private enum State {
        case idle
        case speaking
        case trailing(silenceFrameCount: Int)
    }
    
    private var state: State = .idle
    private let energyThreshold: Float
    private let silenceFrameLimit: Int // 约 380ms 停顿帧数上限
    private var consecutiveSpeechFrames: Int = 0
    
    public init(energyThreshold: Float = -42.0, silenceFrameLimit: Int = 12) {
        self.energyThreshold = energyThreshold
        self.silenceFrameLimit = silenceFrameLimit
    }
    
    public func process(slice: AudioSlice) async -> VADEvent? {
        let db = calculateDB(pcm: slice.pcmData)
        let isSpeech = db > energyThreshold
        
        switch state {
        case .idle:
            if isSpeech {
                consecutiveSpeechFrames += 1
                if consecutiveSpeechFrames >= 3 { // 连续 3 帧判定说话开始
                    state = .speaking
                    consecutiveSpeechFrames = 0
                    return .speechStarted(hostTime: mach_absolute_time())
                }
            } else {
                consecutiveSpeechFrames = 0
            }
            
        case .speaking:
            if !isSpeech {
                state = .trailing(silenceFrameCount: 1)
            }
            
        case .trailing(let count):
            if isSpeech {
                state = .speaking
            } else {
                if count >= silenceFrameLimit {
                    state = .idle
                    return .speechEnded(hostTime: mach_absolute_time())
                } else {
                    state = .trailing(silenceFrameCount: count + 1)
                }
            }
        }
        
        return .volumeUpdate(db: db)
    }
    
    public func reset() async {
        state = .idle
        consecutiveSpeechFrames = 0
    }
    
    private func calculateDB(pcm: [Float]) -> Float {
        guard !pcm.isEmpty else { return -100.0 }
        var sum: Float = 0.0
        for sample in pcm {
            sum += sample * sample
        }
        let rms = sqrt(sum / Float(pcm.count))
        return rms > 0.00001 ? 20 * log10(rms) : -100.0
    }
}
