import Foundation
import AVFoundation
import CoreAudio

@MainActor
public final class BlackHoleTTSEngine: NSObject, @unchecked Sendable, TTSEngine, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var blackHoleDeviceID: AudioDeviceID?
    private var continuation: CheckedContinuation<Void, Never>?

    public override init() {
        super.init()
        synthesizer.delegate = self
        findBlackHoleDevice()
    }

    private func findBlackHoleDevice() {
        var propertySize: UInt32 = 0
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize) == noErr else { return }
        let deviceCount = Int(propertySize) / MemoryLayout<AudioDeviceID>.size
        var deviceIDs = [AudioDeviceID](repeating: 0, count: deviceCount)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propertySize, &deviceIDs) == noErr else { return }

        for id in deviceIDs {
            var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            var nameAddress = AudioObjectPropertyAddress(
                mSelector: kAudioObjectPropertyName,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var unmanagedName: Unmanaged<CFString>? = nil
            if AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &unmanagedName) == noErr, let cfName = unmanagedName?.takeRetainedValue() {
                let nameStr = cfName as String
                if nameStr.contains("BlackHole") {
                    self.blackHoleDeviceID = id
                    print("🔊 已成功关联 BlackHole 虚拟音频设备 ID: \(id)")
                    break
                }
            }
        }
    }

    public func speak(text: String, language: String = "es-ES") async throws {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate

        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            self.continuation = cont
            self.synthesizer.speak(utterance)
        }
    }

    public func stopPlayout() async {
        synthesizer.stopSpeaking(at: .immediate)
        if let cont = continuation {
            continuation = nil
            cont.resume()
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let cont = self.continuation {
                self.continuation = nil
                cont.resume()
            }
        }
    }

    public nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if let cont = self.continuation {
                self.continuation = nil
                cont.resume()
            }
        }
    }
}
