import Foundation

/// 极简线性插值重采样器。不追求音频学教科书级别的滤波质量
/// （不做抗混叠低通滤波），因为目标场景是"语音识别输入"而不是
/// "音乐播放"，人声频段能量集中在几百 Hz 到几 kHz，线性插值造成的
/// 高频混叠噪声对 ASR 识别率影响极小，却能把重采样计算量压到最低，
/// 这对"边说边传"的实时流水线至关重要。
///
/// 如果未来发现识别率因为混叠明显下降，可以替换成基于
/// vDSP (Accelerate) 的多相滤波重采样，接口保持不变。
public struct AudioResampler {

    /// 把任意采样率的单声道 Float32 PCM 转换到目标采样率。
    /// - Parameters:
    ///   - samples: 输入 PCM（[-1, 1] 范围的 Float32）
    ///   - inputSampleRate: 输入采样率，如 48000 / 44100
    ///   - targetSampleRate: 目标采样率，本项目固定传 16000
    /// - Returns: 重采样后的 Float32 PCM
    public static func resample(
        samples: [Float],
        inputSampleRate: Int,
        targetSampleRate: Int
    ) -> [Float] {
        guard !samples.isEmpty else { return [] }
        guard inputSampleRate != targetSampleRate else { return samples }

        let ratio = Double(targetSampleRate) / Double(inputSampleRate)
        let outputCount = max(1, Int(Double(samples.count) * ratio))

        var output = [Float](repeating: 0, count: outputCount)
        let lastIndex = samples.count - 1

        for i in 0..<outputCount {
            let srcPos = Double(i) / ratio
            let srcIndexFloor = Int(srcPos)
            let frac = Float(srcPos - Double(srcIndexFloor))

            if srcIndexFloor >= lastIndex {
                output[i] = samples[lastIndex]
            } else {
                let a = samples[srcIndexFloor]
                let b = samples[srcIndexFloor + 1]
                output[i] = a + (b - a) * frac
            }
        }

        return output
    }

    /// Float32 [-1, 1] PCM 转换为服务端期望的 16-bit signed little-endian PCM 二进制数据。
    /// 这是 WebSocket 传输前的最后一步转换，取代原来"编码成 WAV 文件"的方式，
    /// 省掉 WAV header 开销和 AVAudioFile 磁盘 IO，是本次延迟优化的重要一环。
    public static func floatToPCM16Data(_ samples: [Float]) -> Data {
        var data = Data(capacity: samples.count * 2)
        for sample in samples {
            let clamped = max(-1.0, min(1.0, sample))
            let intValue = Int16(clamped * 32767.0)
            withUnsafeBytes(of: intValue.littleEndian) { bytes in
                data.append(contentsOf: bytes)
            }
        }
        return data
    }
}