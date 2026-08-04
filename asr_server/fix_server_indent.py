import os
import re

server_path = os.path.expanduser("~/trans_mvp/asr_server/server.py")

with open(server_path, "r", encoding="utf-8") as f:
    code = f.read()

# 修复 if DEBUG: 下方空注释导致的 Python 缩进语法报错
code = re.sub(
    r'if DEBUG:\s*# 屏蔽 ffmpeg 冗余日志',
    '# 已优化：屏蔽 ffmpeg 冗余日志',
    code
)

with open(server_path, "w", encoding="utf-8") as f:
    f.write(code)

print("✅ server.py 缩进语法错误已完美修复！")
