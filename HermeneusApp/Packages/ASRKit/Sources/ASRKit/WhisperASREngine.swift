import Foundation
import AVFoundation
import AudioCore

public actor WhisperASREngine: ASREngine {
    private let serverURL: URL
    
    public init(serverURL: URL = URL(string: "http://localhost:8080/inference")!) {
        self.serverURL = serverURL
    }
    
    public func transcribe(segment: Segment, language: String = "zh") async throws -> AsyncThrowingStream<ASRUpdate, Error> {
        guard let wavData = createStandardWavData(from: segment.audio.pcmData, sampleRate: segment.audio.sampleRate) else {
            return AsyncThrowingStream { $0.finish() }
        }
        
        var request = URLRequest(url: serverURL)
        request.httpMethod = "POST"
        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 20.0
        
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"language\"\r\n\r\n".data(using: .utf8)!)
        body.append("\(language)\r\n".data(using: .utf8)!)
        
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(wavData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        
        request.httpBody = body
        
        return AsyncThrowingStream { continuation in
            Task {
                do {
                    let (data, response) = try await URLSession.shared.data(for: request)
                    guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                        continuation.yield(.final("", meta: ASRMeta(confidence: 0, durationMs: segment.audio.durationMs)))
                        continuation.finish()
                        return
                    }
                    
                    if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let text = json["text"] as? String {
                        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
                        continuation.yield(.final(cleaned, meta: ASRMeta(confidence: 0.95, durationMs: segment.audio.durationMs)))
                    } else {
                        continuation.yield(.final("", meta: ASRMeta(confidence: 0, durationMs: segment.audio.durationMs)))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
    
    public func abortCurrentTask() async {}
    
    private func createStandardWavData(from pcm: [Float], sampleRate: Int) -> Data? {
        guard !pcm.isEmpty else { return nil }
        
        let rate = Double(sampleRate > 0 ? sampleRate : 44100)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false) else { return nil }
        guard let pcmBuffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(pcm.count)) else { return nil }
        pcmBuffer.frameLength = AVAudioFrameCount(pcm.count)
        
        if let channelData = pcmBuffer.floatChannelData?[0] {
            for i in 0..<pcm.count {
                channelData[i] = pcm[i]
            }
        }
        
        let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        defer { try? FileManager.default.removeItem(at: tempURL) }
        
        do {
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: rate,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsFloatKey: false
            ]
            let audioFile = try AVAudioFile(forWriting: tempURL, settings: settings)
            try audioFile.write(from: pcmBuffer)
            return try Data(contentsOf: tempURL)
        } catch {
            return nil
        }
    }
}
