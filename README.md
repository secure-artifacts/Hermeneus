# Hermeneus 传译者 使用与安装指南

## 快速流程

```text
首次安装（仅一次）
    │
    ├── 安装 Homebrew 与 Ollama
    ├── 下载 Qwen 翻译模型
    ├── 安装 Hermeneus Full 版本
    └── 配置一键启动脚本
            │
            ▼
日常使用
    │
    └── 打开终端 → 输入 `hermeneus`
            │
            ▼
实时同声传译
一、首次安装（仅需配置一次）
Step 1 打开终端（Terminal）
按 Command (⌘) + Space 打开 Spotlight，输入 Terminal 或 终端，按回车。

或打开：应用程序 → 实用工具 → Terminal

Step 2 安装 Homebrew 与 Ollama
Bash
/bin/bash -c "$(curl -fsSL [https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh](https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh))"
brew install ollama
Step 3 下载 AI 翻译模型
Bash
nohup ollama serve > /dev/null 2>&1 &
sleep 2

ollama pull qwen2.5:3b
注意：请确保 Hermeneus 设置界面里的模型名称与 ollama list 中一致（如 qwen2.5:3b）。若使用外置硬盘存储模型，脚本会自动识别并挂载。

Step 4 安装 Hermeneus Full
下载最新的 Hermeneus-macOS-vX.X.X-Full.dmg，双击打开，将 Hermeneus 拖入 Applications（应用程序）。

Step 5 配置一键通用启动
在终端一次性粘贴运行以下脚本，自动写入自适应启动引导器并绑定 hermeneus 命令：

Bash
cat << 'EOF' > ~/Documents/Hermeneus_start.sh
#!/bin/bash
#
# Hermeneus_start.sh —— 通用环境启动引导脚本
# 适用于任意 Mac 设备，自适应处理 Ollama 路径、Gatekeeper 隔离与 ASR 探活
#

set -uo pipefail

readonly APP_NAME="Hermeneus"
readonly APP_PATH="/Applications/${APP_NAME}.app"
readonly ASR_BIN="${APP_PATH}/Contents/Resources/asr_server"
readonly ASR_LOG="/tmp/asr_server.log"
readonly OLLAMA_LOG="/tmp/ollama.log"

readonly ASR_HEALTH_URLS=("[http://127.0.0.1:8080](http://127.0.0.1:8080)" "[http://127.0.0.1:8081](http://127.0.0.1:8081)")
readonly ASR_HEALTH_TIMEOUT_SEC=30
readonly ASR_PORTS=(8080 8081)

readonly OLLAMA_API_URL="[http://127.0.0.1:11434/api/tags](http://127.0.0.1:11434/api/tags)"
readonly OLLAMA_HEALTH_TIMEOUT_SEC=10
readonly OLLAMA_PORT=11434

# 外置模型盘候选路径
readonly CUSTOM_MODEL_CANDIDATES=(
    "/Volumes/untitled/APP/ollama models"
    "/Volumes/Hermeneus/ollama models"
)

log()  { printf '%s\n' "$1"; }
ok()   { printf '✅ %s\n' "$1"; }
warn() { printf '⚠️  %s\n' "$1" >&2; }
fail() { printf '❌ %s\n' "$1" >&2; }
step() { printf '\n%s\n' "$1"; }

port_in_use() {
    lsof -i ":$1" -sTCP:LISTEN -t &>/dev/null
}

kill_port() {
    local pids
    pids=$(lsof -ti ":$1" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "${pids}" ]; then
        log "    正在释放被占用的端口 $1 (PID:${pids})..."
        kill -9 ${pids} 2>/dev/null || true
    fi
}

# 1. 清理残留进程与端口
step "🧹 [1/5] 清理残留进程与端口占用..."
pkill -f "asr_server" 2>/dev/null && log "    已终止残留的 asr_server 进程" || true
pkill -f "ollama serve" 2>/dev/null && log "    已终止残留的 ollama serve 进程" || true
sleep 0.5
for p in "${ASR_PORTS[@]}" "${OLLAMA_PORT}"; do
    if port_in_use "$p"; then kill_port "$p"; fi
done
ok "残留进程与端口清理完毕"

# 2. 定位并拉起 Ollama
step "🚀 [2/5] 检查系统环境与 Ollama 服务..."
OLLAMA_BIN=""
if command -v ollama &>/dev/null; then
    OLLAMA_BIN="$(command -v ollama)"
else
    for candidate in "/opt/homebrew/bin/ollama" "/usr/local/bin/ollama" "/usr/bin/ollama"; do
        if [ -x "$candidate" ]; then OLLAMA_BIN="$candidate"; break; fi
    done
fi

if [ -z "$OLLAMA_BIN" ]; then
    fail "未检测到 ollama，请先执行 'brew install ollama'！"
    exit 1
fi

# 外置盘挂载检测
for candidate in "${CUSTOM_MODEL_CANDIDATES[@]}"; do
    if [ -d "$candidate" ]; then
        export OLLAMA_MODELS="$candidate"
        log "    📦 已挂载外置模型路径: ${OLLAMA_MODELS}"
        break
    fi
done

nohup "$OLLAMA_BIN" serve > "$OLLAMA_LOG" 2>&1 &
disown

# Ollama API 探活
OLLAMA_READY=0
for i in $(seq 1 "$OLLAMA_HEALTH_TIMEOUT_SEC"); do
    if curl -s -o /dev/null --max-time 1 "$OLLAMA_API_URL"; then
        OLLAMA_READY=1
        ok "Ollama API 已就绪！"
        break
    fi
    sleep 1
done

# 3. 清理 macOS 隔离属性
step "🛡️ [3/5] 清理 macOS 隔离标记 (Quarantine)..."
if [ -d "$APP_PATH" ]; then
    xattr -cr "$APP_PATH" 2>/dev/null || true
    ok "已清除 ${APP_PATH} 隔离标记"
else
    fail "未找到 ${APP_PATH}，请先将 App 拖入应用程序文件夹"
    exit 1
fi

# 4. 拉起 ASR 引擎与健康探活
step "🎙️ [4/5] 启动 ASR 语音识别引擎..."
if [ ! -f "$ASR_BIN" ]; then
    fail "未在 App 包内找到 asr_server: ${ASR_BIN}"
    exit 1
fi

chmod +x "$ASR_BIN"
"$ASR_BIN" > "$ASR_LOG" 2>&1 &
ASR_PID=$!
disown

ASR_READY=0
for i in $(seq 1 "$ASR_HEALTH_TIMEOUT_SEC"); do
    if ! kill -0 "$ASR_PID" 2>/dev/null; then
        fail "ASR 进程在探活期间意外退出，日志如下："
        tail -n 30 "$ASR_LOG" 2>/dev/null
        break
    fi
    for url in "${ASR_HEALTH_URLS[@]}"; do
        code=$(curl -s -o /dev/null -w '\%{http_code}' --max-time 1 "$url" 2>/dev/null)
        if [ -n "$code" ] && [ "$code" != "000" ]; then
            ASR_READY=1
            break 2
        fi
    done
    sleep 1
done

if [ "$ASR_READY" -eq 0 ]; then
    fail "ASR 引擎启动超时，请检查日志: ${ASR_LOG}"
    exit 1
fi
ok "ASR 引擎初始化成功，端口已就绪！"

# 5. 打开 App
step "🖥️ [5/5] 启动同传 App 界面..."
if open -a "$APP_NAME"; then
    ok "启动流程完成！🎉"
else
    fail "无法拉起 ${APP_NAME}.app"
    exit 1
fi
EOF

chmod +x ~/Documents/Hermeneus_start.sh

# 绑定 alias 快捷指令
if ! grep -q "alias hermeneus=" ~/.zshrc 2>/dev/null; then
    echo "alias hermeneus='~/Documents/Hermeneus_start.sh'" >> ~/.zshrc
fi
source ~/.zshrc
二、日常使用
打开终端，直接执行：

Bash
hermeneus
Hermeneus 启动脚本将自动完成：

释放残留端口并启动 Ollama（自动适配外置盘/本地模型路径）

清除 macOS Gatekeeper 安全隔离属性

启动并检测内嵌 Python ASR 引擎健康端口

探活成功后拉起 Hermeneus 悬浮字幕界面

对方讲话
系统音频捕获引擎（结合 BlackHole 虚拟声卡隔离防自激）自动识别并实时实时渲染翻译。

自己讲话
长按 Option（⌥） 键录音，松开后自动进行语音识别、翻译并播放给对方。

三、软件更新
更新软件时，下载最新的 Hermeneus-macOS-vX.X.X-Full.dmg 覆盖写入 /Applications 文件夹即可，无需重新配置 Hermeneus_start.sh 脚本。

更新完成后继续运行：

Bash
hermeneus
系统要求
Apple Silicon Mac (M1 / M2 / M3 / M4) 或 Intel Mac

macOS 13.0+

Homebrew & Ollama

Qwen2.5 语言模型

麦克风权限 & 屏幕录制权限（系统声音捕获依赖）