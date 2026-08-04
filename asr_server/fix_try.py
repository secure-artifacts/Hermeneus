import os

po_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/PipelineOrchestrator.swift")

with open(po_path, "r", encoding="utf-8") as f:
    content = f.read()

# 把漏掉 try 的那行代码补上 try?
old_code = "await systemCapturer.stopCapture()"
new_code = "try? await systemCapturer.stopCapture()"

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(po_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ 已成功修复 stopSession 中的 try? 缺失问题！")
else:
    print("⚠️ 未找到目标代码，可能已经修复过。")
