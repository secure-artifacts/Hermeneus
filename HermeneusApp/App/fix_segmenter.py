import os

po_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/PipelineOrchestrator.swift")

with open(po_path, "r", encoding="utf-8") as f:
    content = f.read()

# 优化切片参数：拉长缓冲、放宽静音判定
old_segmenter = """            if buffer.count >= Int(Double(sampleRate) * 2.2) {
                let chunk = buffer
                buffer.removeAll()
                return chunk
            }
        } else {
            if !buffer.isEmpty {
                silenceFrames += 1
                buffer.append(contentsOf: pcm)

                if silenceFrames >= silenceFramesToFinalize {
                    let chunk = buffer
                    buffer.removeAll()
                    silenceFrames = 0
                    if chunk.count >= Int(Double(sampleRate) * 0.7) {
                        return chunk
                    }
                }
            }
        }"""

new_segmenter = """            // 优化：允许最大 4.5 秒的长单句，让意思表达完整
            if buffer.count >= Int(Double(sampleRate) * 4.5) {
                let chunk = buffer
                buffer.removeAll()
                return chunk
            }
        } else {
            if !buffer.isEmpty {
                silenceFrames += 1
                buffer.append(contentsOf: pcm)

                // 优化：要求 10 帧 (~1.0s) 的持续静音才认为整句结束
                if silenceFrames >= 10 {
                    let chunk = buffer
                    buffer.removeAll()
                    silenceFrames = 0
                    // 优化：低于 1.2 秒的碎音不独立成段
                    if chunk.count >= Int(Double(sampleRate) * 1.2) {
                        return chunk
                    }
                }
            }
        }"""

if "buffer.count >= Int(Double(sampleRate) * 2.2)" in content:
    content = content.replace(old_segmenter, new_segmenter)
    with open(po_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 智能长句断句参数优化完成！")
else:
    print("⚠️ 未找到旧参数或已经优化过。")
