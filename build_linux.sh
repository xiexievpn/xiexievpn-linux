#!/bin/bash
# XiexieVPN Linux 客户端构建脚本
# 使用 Python 虚拟环境解决 externally-managed-environment 错误

echo "=== 开始构建 Linux 客户端 ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 1. 安装系统依赖
# python3-pil.imagetk 是让 Tkinter 能显示图片的关键
echo "安装系统依赖..."
sudo apt update
sudo apt install -y python3-venv python3-tk python3-pil python3-pil.imagetk python3-full unzip wget

# 2. 创建并激活虚拟环境
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "创建虚拟环境..."
    python3 -m venv "$VENV_DIR" --system-site-packages
fi
source "$VENV_DIR/bin/activate"

# 3. 在虚拟环境中安装 Python 依赖
echo "安装 Python 依赖..."
pip install --upgrade pip
pip install pyinstaller requests pillow

# 4. 准备目录结构
echo "准备构建目录..."
mkdir -p build_work
cp main_linux.py build_work/
cp languages.json build_work/

# 复制图标文件 (优先 .ico, 其次 .png, 最后从 winclient 复制)
if [ -f "favicon.ico" ]; then
    cp favicon.ico build_work/
elif [ -f "favicon.png" ]; then
    cp favicon.png build_work/favicon.ico
elif [ -f "../winclient/favicon.ico" ]; then
    cp ../winclient/favicon.ico build_work/
else
    echo "警告: 未找到图标文件，创建空占位符"
    touch build_work/favicon.ico
fi

# 5. 下载并提取 Linux 版 Xray 核心 (如未下载)
if [ ! -f "build_work/xray" ]; then
    echo "下载 Xray Core..."
    wget -O xray.zip https://github.com/XTLS/Xray-core/releases/download/v25.3.6/Xray-linux-64.zip
    unzip -o xray.zip xray geoip.dat geosite.dat -d build_work/
    chmod +x build_work/xray
fi

# 6. 执行打包
echo "正在打包..."
cd build_work

# 注意: 
# --add-data 参数在 Linux 下分隔符是冒号 :
# --hidden-import PIL._tkinter_finder 解决 Pillow+Tkinter 打包问题
pyinstaller --onefile --windowed --clean \
    --name "xiexievpn_linux" \
    --add-data "languages.json:." \
    --add-data "favicon.ico:." \
    --add-data "xray:." \
    --add-data "geoip.dat:." \
    --add-data "geosite.dat:." \
    --hidden-import PIL._tkinter_finder \
    main_linux.py

# 7. 退出虚拟环境
deactivate

echo ""
echo "=== 构建完成 ==="
echo "最终文件: $SCRIPT_DIR/build_work/dist/xiexievpn_linux"
echo ""
echo "测试运行: ./build_work/dist/xiexievpn_linux"