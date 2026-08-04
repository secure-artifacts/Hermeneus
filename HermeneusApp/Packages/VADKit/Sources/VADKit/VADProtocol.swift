import Foundation
import AudioCore

public enum VADEvent: Sendable {
    case speechStarted(hostTime: UInt64)
    case speechEnded(hostTime: UInt64)
    case volumeUpdate(db: Float)
}

public protocol VADEngine: Actor {
    func process(slice: AudioSlice) async -> VADEvent?
    func reset() async
}
