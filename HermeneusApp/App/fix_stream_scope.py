import os

engine_path = os.path.expanduser("~/trans_mvp/AILiveInterpreter/Packages/TranslateKit/Sources/TranslateKit/OllamaTranslationEngine.swift")

with open(engine_path, "r", encoding="utf-8") as f:
    code = f.read()

# 将未定义的 lineStream 修正为 Swift 原生的 bytes.lines 数据流
code = code.replace("for try await line in lineStream {", "for try await line in bytes.lines {")

with open(engine_path, "w", encoding="utf-8") as f:
    f.write(code)

print("✅ TranslateKit: lineStream 变量作用域修复成功！")
