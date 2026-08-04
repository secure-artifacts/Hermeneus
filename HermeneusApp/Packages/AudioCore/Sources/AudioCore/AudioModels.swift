import Foundation

public enum Channel: String, Codable, Sendable {
    case remote // 对方 (ScreenCaptureKit 捕获)
    case local  // 我方 (物理麦克风)
}

public struct SegmentID: Hashable, Sendable {
    public let channel: Channel
    public let seq: UInt64
    
    public init(channel: Channel, seq: UInt64) {
        self.channel = channel
        self.seq = seq
    }
}

public struct AudioSlice: Sendable {
    public let pcmData: [Float]
    public let sampleRate: Int
    public let durationMs: Double
    
    public init(pcmData: [Float], sampleRate: Int = 16000) {
        self.pcmData = pcmData
        self.sampleRate = sampleRate
        self.durationMs = (Double(pcmData.count) / Double(sampleRate)) * 1000.0
    }
}

public struct Segment: Sendable {
    public let id: SegmentID
    public let audio: AudioSlice
    public let startHostTime: UInt64
    public var isFinal: Bool
    
    public init(id: SegmentID, audio: AudioSlice, startHostTime: UInt64 = mach_absolute_time(), isFinal: Bool = false) {
        self.id = id
        self.audio = audio
        self.startHostTime = startHostTime
        self.isFinal = isFinal
    }
}
