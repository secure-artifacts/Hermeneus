# -*- mode: python ; coding: utf-8 -*-
"""
asr_server.spec —— Hermeneus ASR 引擎 PyInstaller onefile 打包规格

崩溃根因（已在 CI 日志中复现确认）：
    AttributeError: module 'pkg_resources' has no attribute 'NullProvider'
    File "pyi_rth_pkgres.py", line 85, in _pyi_rthook

这是 setuptools>=70.0.0 之后的已知生态问题（pypa/setuptools#4374,
pyinstaller/pyinstaller#8554）：setuptools 70 重构了 pkg_resources，砍掉了
NullProvider / pkg_resources.extern 等旧 API，而 PyInstaller 内置的运行时钩子
pyi_rth_pkgres.py 仍假设这些 API 存在，于是二进制在真正执行 server.py 之前，
就在钩子阶段直接崩溃。

真正的修复点在【构建环境】：将 setuptools 锁定在 <70（推荐 69.5.1，是社区验证
过的最后一个可用版本），而不是在这个 spec 文件里"猜"。
本文件做的是双重保险：
    1. 显式声明 pkg_resources 相关子模块为 hiddenimports，避免任何一次 CI 缓存
       残留或依赖漂移导致收集不全；
    2. 精准 collect_all 重型依赖，避免遗漏 .so / 模型配置文件；
    3. 排除测试 / 文档 / 可视化等冗余子模块，控制体积、减少无关崩溃面。
"""

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

block_cipher = None

# --------------------------------------------------------------------------
# 1. 重型 AI 依赖：完整收集 datas / binaries / hiddenimports
# --------------------------------------------------------------------------
HEAVY_PACKAGES = [
    "funasr",
    "faster_whisper",
    "torch",
    "ctranslate2",
    "modelscope",
]

datas = []
binaries = []
hiddenimports = []

for pkg in HEAVY_PACKAGES:
    try:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
        print(f"[spec] collect_all 完成: {pkg} "
              f"(+{len(pkg_datas)} datas, +{len(pkg_binaries)} binaries, "
              f"+{len(pkg_hiddenimports)} hiddenimports)")
    except Exception as exc:  # noqa: BLE001 - 打包期诊断，不吞掉关键包时才继续
        print(f"[spec] 警告：collect_all 处理 {pkg} 失败，已跳过: {exc}")

# ctranslate2 的核心推理库是动态链接的 .dylib，collect_all 有时抓不全，显式补充
binaries += collect_dynamic_libs("ctranslate2")

# --------------------------------------------------------------------------
# 2. pkg_resources：显式声明命名空间子模块
#    （防止运行时钩子在解析 entry_points / distribution 元数据时因懒加载
#     缺失而报错；真正杜绝 NullProvider 崩溃仍以锁定 setuptools<70 为准）
# --------------------------------------------------------------------------
hiddenimports += collect_submodules("pkg_resources")
hiddenimports += [
    "pkg_resources.extern",
    "pkg_resources._vendor",
    "pkg_resources._vendor.packaging",
    "funasr.models",
    "funasr.utils",
    "torch._C",
    "torch.utils.checkpoint",
]

# --------------------------------------------------------------------------
# 3. 裁剪：测试 / 文档 / 可视化 / 交互式解释器相关模块
# --------------------------------------------------------------------------
excludes = [
    "torch.utils.tensorboard",
    "torch.testing",
    "torch.utils.data.datapipes",
    "matplotlib",
    "tkinter",
    "IPython",
    "notebook",
    "jupyter",
    "pytest",
    "sphinx",
    "setuptools.tests",
    "pkg_resources.tests",
]

a = Analysis(
    ["server.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile 模式：不做 COLLECT，产物直接是 dist/asr_server 单文件可执行程序，
# 与 Hermeneus_start.sh / build_dmg.yml 中约定的路径保持一致。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="asr_server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
