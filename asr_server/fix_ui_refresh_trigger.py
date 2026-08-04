import os

po_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/App/Sources/PipelineOrchestrator.swift")

with open(po_path, "r", encoding="utf-8") as f:
    po_code = f.read()

# 强行给数据写入方法加上 UI 主线程刷新通知 (DispatchQueue.main.async { self.objectWillChange.send() })
if "DispatchQueue.main.async { self.objectWillChange.send() }" not in po_code:
    # 替换追加/更新字幕时的通知机制
    po_code = po_code.replace(
        "remoteBuffer.addTurn",
        "DispatchQueue.main.async { self.objectWillChange.send() }\n        remoteBuffer.addTurn"
    )
    po_code = po_code.replace(
        "localBuffer.addTurn",
        "DispatchQueue.main.async { self.objectWillChange.send() }\n        localBuffer.addTurn"
    )
    po_code = po_code.replace(
        "remoteBuffer.updateLastTurn",
        "DispatchQueue.main.async { self.objectWillChange.send() }\n        remoteBuffer.updateLastTurn"
    )
    po_code = po_code.replace(
        "localBuffer.updateLastTurn",
        "DispatchQueue.main.async { self.objectWillChange.send() }\n        localBuffer.updateLastTurn"
    )

with open(po_path, "w", encoding="utf-8") as f:
    f.write(po_code)

print("✅ PipelineOrchestrator.swift: 主线程 UI 强制刷新通道已打通！")
