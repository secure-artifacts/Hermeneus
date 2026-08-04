import Foundation

public actor AudioRingBuffer {
    private var buffer: [Float] = []
    private let capacity: Int
    
    public init(capacity: Int) {
        self.capacity = capacity
    }
    
    public func write(_ samples: [Float]) {
        buffer.append(contentsOf: samples)
        if buffer.count > capacity {
            buffer.removeFirst(buffer.count - capacity)
        }
    }
    
    public func readLatest(count: Int) -> [Float] {
        guard !buffer.isEmpty else { return [] }
        let readCount = min(count, buffer.count)
        let result = Array(buffer.suffix(readCount))
        buffer.removeLast(readCount)
        return result
    }
}
