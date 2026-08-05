# Hermeneus 传译者 使用与安装指南

## 快速流程

``` text
首次安装（仅一次）
    │
    ├── 安装 Homebrew 与 Ollama
    ├── 下载 Qwen 翻译模型
    ├── 安装 Hermeneus Full 版本
    └── 配置一键启动命令
            │
            ▼
日常使用
    │
    └── 打开终端 → 输入 `hermeneus`
            │
            ▼
实时同声传译
```

------------------------------------------------------------------------

# 一、首次安装（仅需配置一次）

## Step 1 打开终端（Terminal）

-   按 **Command (⌘) + Space** 打开 Spotlight，输入 **Terminal** 或
    **终端**，按回车。
-   或打开：**应用程序 → 实用工具 → Terminal**

## Step 2 安装 Homebrew 与 Ollama（命令行版）

``` bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install ollama
```

## Step 3 下载 AI 翻译模型

``` bash
nohup ollama serve > /dev/null 2>&1 &
sleep 2

ollama pull qwen2.5:3b
ollama cp qwen2.5:3b qwen2.5
```

## Step 4 安装 Hermeneus Full

下载：

`Hermeneus-macOS-vX.X.X-Full.dmg`

双击打开 `.dmg`，将 **Hermeneus** 拖入 **Applications（应用程序）**。

## Step 5 配置一键启动

``` bash
cat << 'EOF' > ~/Documents/Hermeneus_start.sh
#!/bin/bash

if ! pgrep -f "ollama serve" > /dev/null; then
    nohup ollama serve > /dev/null 2>&1 &
    sleep 2
fi

open -a Hermeneus
EOF

chmod +x ~/Documents/Hermeneus_start.sh
echo "alias hermeneus='~/Documents/Hermeneus_start.sh'" >> ~/.zshrc
source ~/.zshrc
```

------------------------------------------------------------------------

# 二、日常使用

打开终端，执行：

``` bash
hermeneus
```

Hermeneus 将自动：

-   启动 Ollama（若未运行）
-   启动 Hermeneus
-   打开悬浮实时字幕

### 对方讲话

系统自动识别并实时翻译。

### 自己讲话

长按 **Option（⌥）** 键录音，松开后自动识别、翻译并播放目标语言。

------------------------------------------------------------------------

# 三、软件更新

首次安装完成后，仅需下载：

`Hermeneus-vX.X.X-Lite.zip`

解压后：

1.  打开 `.dmg`
2.  拖入 **Applications**
3.  选择 **替换**

然后继续使用：

``` bash
hermeneus
```

------------------------------------------------------------------------

# 系统要求

-   Apple Silicon Mac
-   Homebrew
-   Ollama
-   Qwen2.5 模型
-   麦克风权限
-   屏幕录制权限
