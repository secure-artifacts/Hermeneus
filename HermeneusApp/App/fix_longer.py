import os

po_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/PipelineOrchestrator.swift")

with open(po_path, "r", encoding="utf-8") as f:
    content = f.read()

# 替换为更长句子的缓冲参数
old_code = """            // 优化：允许最大 4.5 秒的长单句，让意思表达完整
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

new_code = """            // 深度优化：允许最大 7.0 秒超长句子，完整翻译长从句
            if buffer.count >= Int(Double(sampleRate) * 7.0) {
                let chunk = buffer
                buffer.removeAll()
                return chunk
            }
        } else {
            if !buffer.isEmpty {
                silenceFrames += 1
                buffer.append(contentsOf: pcm)

                // 深度优化：要求 15 帧 (~1.5s) 彻底静音才断句，容忍换气与卡顿
                if silenceFrames >= 15 {
                    let chunk = buffer
                    buffer.removeAll()
                    silenceFrames = 0
                    // 深度优化：低于 1.5 秒的碎片词不独立成段
                    if chunk.count >= Int(Double(sampleRate) * 1.5) {
                        return chunk
                    }
                }
            }
        }"""

if "buffer.count >= Int(Double(sampleRate) * 4.5)" in content:
    content = content.replace(old_code, new_code)
    with open(po_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 7.0 秒超长连贯句子模式更新完成！")
else:
    # 兜底直接正则全替换
    import re
    content = re.sub(r'Double\(sampleRate\) \* \d+\.\d+', 'Double(sampleRate) * 7.0', content, count=1)
    content = re.sub(r'silenceFrames >= \d+', 'silenceFrames >= 15', content, count=1)
    with open(po_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 参数已强行修正为 7.0s 长句模式！")
