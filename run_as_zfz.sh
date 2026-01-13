#!/bin/bash
# 以 zfz 用户身份运行 Xiexie VPN 客户端
# 用于: 在 root xRDP 会话中降权启动 GUI 程序

TARGET_USER="zfz"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 检测程序路径（优先使用编译后的二进制，否则用 python 脚本）
if [ -f "$SCRIPT_DIR/dist/xiexievpn_linux" ]; then
    PROGRAM="$SCRIPT_DIR/dist/xiexievpn_linux"
elif [ -f "$SCRIPT_DIR/xiexievpn_linux" ]; then
    PROGRAM="$SCRIPT_DIR/xiexievpn_linux"
else
    PROGRAM="python3 $SCRIPT_DIR/main_linux.py"
fi

echo "程序路径: $PROGRAM"
echo "目标用户: $TARGET_USER"
echo "DISPLAY: $DISPLAY"

# 允许目标用户访问当前 X 显示
xhost +SI:localuser:$TARGET_USER 2>/dev/null
# 也允许本地连接（备用）
xhost +local: 2>/dev/null

echo "正在以 $TARGET_USER 身份启动..."

# 以 zfz 身份运行，尝试多种 DBus 启动方式
su - $TARGET_USER -c "
    export DISPLAY='$DISPLAY'
    export XDG_CURRENT_DESKTOP='GNOME'
    export XDG_SESSION_TYPE='x11'
    export HOME='/home/$TARGET_USER'
    cd '$SCRIPT_DIR'
    
    # 尝试不同的 DBus 启动方式
    if command -v dbus-launch &> /dev/null; then
        echo '使用 dbus-launch 启动...'
        dbus-launch --exit-with-session $PROGRAM
    elif command -v dbus-run-session &> /dev/null; then
        echo '使用 dbus-run-session 启动...'
        dbus-run-session -- $PROGRAM
    else
        echo '无 DBus 启动器，直接运行（系统代理可能不生效）...'
        $PROGRAM
    fi
"
