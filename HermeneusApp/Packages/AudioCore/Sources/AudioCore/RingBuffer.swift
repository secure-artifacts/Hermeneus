import Foundation

public final class RingBuffer: @unchecked Sendable {
    private var buffer: [Float]
    private let capacity: Int
    private var writeIndex: Int = 0
    private var readIndex: Int = 0
    private let lock = NSLock() // 用于读写索引的安全互斥
    
    public init(capacity: Int = 48000 * 60) { // 默认 60 秒 48kHz 单声道容量 (~11.5MB)
        self.capacity = capacity
        self.buffer = [Float](repeating: 0, count: capacity)
    }
    
    public func write(_ samples: [Float]) {
        lock.lock()
        defer { lock.unlock() }
        
        for sample in samples {
            buffer[writeIndex] = sample
            writeIndex = (writeIndex + 1) % capacity
            if writeIndex == readIndex {
                // 覆盖旧数据（Drop Oldest）
                readIndex = (readIndex + 1) % capacity
            }
        }
    }
    
    public func readLatest(count: Int) -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        
        let available = availableReadCount()
        let toRead = min(count, available)
        guard toRead > 0 else { return [] }
        
        var result = [Float](repeating: 0, count: toRead)
        var startIndex = (writeIndex - toRead + capacity) % capacity
        
        for i in 0..<toRead {
            result[i] = buffer[startIndex]
            startIndex = (startIndex + 1) % capacity
        }
        return result
    }
    
    public func availableReadCount() -> Int {
        if writeIndex >= readIndex {
            return writeIndex - readIndex
        } else {
            return capacity - readIndex + writeIndex
        }
    }
}
