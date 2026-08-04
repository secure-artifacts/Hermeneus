#!/bin/bash
echo "1/3 正在后台启动 Ollama..."
ollama run qwen2.5 > /dev/null 2>&1 &

echo "2/3 正在后台启动 Python ASR 服务端..."
python3 ~/Documents/Hermeneus/asr_server/server.py &

sleep 3
echo "3/3 正在启动 App 界面..."
cd ~/Documents/Hermeneus/HermeneusApp/App && swift run
