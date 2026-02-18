import tkinter as tk
from tkinter import messagebox, Menu, ttk
import subprocess, os, sys
import requests
import json
import urllib.parse
import threading
import time
import fcntl
import stat
import shutil
import glob
import socket
import concurrent.futures
import base64
from pathlib import Path

# ================= 自动降权 (Root Auto-Demotion) =================

def detect_desktop_user():
    """
    检测控制当前 GNOME 桌面会话的原始用户。
    返回用户名，如果无法检测则返回 None。
    """
    # 方法1: 检查 SUDO_USER (如果是通过 sudo 运行的)
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return sudo_user
    
    # 方法2: 检查 LOGNAME / USER 环境变量（某些 su 场景）
    for var in ['LOGNAME', 'USER']:
        user = os.environ.get(var)
        if user and user != 'root':
            return user
    
    # 方法3: 查找 gnome-session / Xorg 进程的所有者
    try:
        result = subprocess.run(
            ['ps', '-eo', 'user,comm'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                user, comm = parts[0], parts[1]
                if user != 'root' and comm in ('gnome-session', 'gnome-shell', 'Xorg', 'Xwayland'):
                    return user
    except:
        pass
    
    # 方法4: 检查 DISPLAY 对应的 X 服务器
    display = os.environ.get('DISPLAY', ':0')
    display_num = display.replace(':', '').split('.')[0] or '0'
    try:
        # 检查 /tmp/.X11-unix/X{n} 的所有者
        x_socket = f'/tmp/.X11-unix/X{display_num}'
        if os.path.exists(x_socket):
            import pwd
            uid = os.stat(x_socket).st_uid
            user = pwd.getpwuid(uid).pw_name
            if user != 'root':
                return user
    except:
        pass
    
    # 方法5: 检查 /home 下有 .config 目录的用户
    try:
        for entry in os.listdir('/home'):
            home_path = f'/home/{entry}'
            if os.path.isdir(home_path) and os.path.isdir(f'{home_path}/.config'):
                return entry
    except:
        pass
    
    return None

def auto_demotion():
    """
    如果以 root 身份运行，自动降权到桌面用户。
    这个函数必须在程序最开始调用，在任何 Tkinter 初始化之前。
    """
    if os.geteuid() != 0:
        return  # 非 root，无需降权
    
    target_user = detect_desktop_user()
    if not target_user:
        # 无法检测到桌面用户，打印错误并退出
        print("错误: 以 root 身份运行，但无法检测到桌面用户。", file=sys.stderr)
        print("Error: Running as root, but cannot detect desktop user.", file=sys.stderr)
        print("请使用普通用户运行此程序。", file=sys.stderr)
        sys.exit(1)
    
    print(f"检测到桌面用户: {target_user}")
    print(f"正在以 {target_user} 身份重新启动...")
    
    # 获取当前 DISPLAY
    display = os.environ.get('DISPLAY', ':0')
    
    # 1. 允许目标用户访问 X 显示
    subprocess.run(['xhost', f'+SI:localuser:{target_user}'], 
                   stderr=subprocess.DEVNULL, timeout=5)
    subprocess.run(['xhost', '+local:'], 
                   stderr=subprocess.DEVNULL, timeout=5)
    
    # 2. 确定程序路径
    # PyInstaller 打包后，sys.executable 是二进制文件本身
    # 开发时，sys.executable 是 python 解释器
    if getattr(sys, 'frozen', False):
        # 打包后的二进制
        program = sys.executable
        program_args = sys.argv[1:]  # 传递原始参数
    else:
        # 开发环境，使用 python3 运行脚本
        program = f"python3 {os.path.abspath(__file__)}"
        program_args = sys.argv[1:]
    
    # 3. 构建以普通用户身份运行的命令
    # 设置必要的环境变量并使用 dbus-launch 启动 DBus 会话
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    args_str = ' '.join(f"'{arg}'" for arg in program_args) if program_args else ''
    
    inner_cmd = f"""
export DISPLAY='{display}'
export XDG_CURRENT_DESKTOP='GNOME'
export XDG_SESSION_TYPE='x11'
export HOME='/home/{target_user}'
cd '{script_dir}'
if command -v dbus-launch &> /dev/null; then
    exec dbus-launch --exit-with-session {program} {args_str}
elif command -v dbus-run-session &> /dev/null; then
    exec dbus-run-session -- {program} {args_str}
else
    exec {program} {args_str}
fi
"""
    
    # 4. 使用 su 切换用户并执行
    os.execlp('su', 'su', '-', target_user, '-c', inner_cmd)

# ================= 配置与常量 =================
CURRENT_VERSION = "2.0.0" 
APP_NAME = "xiexievpn"
SUB_DOMAIN = "sub.xiexievpn.com"

# Linux 配置路径遵循 XDG 标准: ~/.config/xiexievpn
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "config.json"       # Xray (VLESS) 配置
HY2_CONFIG_FILE = CONFIG_DIR / "hy2_config.json" # Hysteria2 配置
UUID_FILE = CONFIG_DIR / "uuid.txt"
AUTOSTART_FILE = CONFIG_DIR / "autostart_state.txt"
AUTO_LOGIN_FILE = CONFIG_DIR / "auto_login.txt"  # 标记是否自动登录
XRAY_BIN = CONFIG_DIR / "xray"
HY2_BIN = CONFIG_DIR / "hysteria"

# 区域代码映射
REGION_TO_FLAG = {
    "us-west-2": "us", "ap-northeast-2": "jp", "ap-northeast-1": "jj",
    "ap-southeast-1": "si", "ap-southeast-2": "au", "ap-south-1": "in",
    "ca-central-1": "ca", "eu-central-1": "ge", "eu-west-1": "ir",
    "eu-west-2": "ki", "eu-west-3": "fr", "eu-north-1": "sw"
}

# 全局状态
proxy_state = 0            
pending_autostart = False
current_region = None
current_uuid = None
current_protocol = None     # 当前使用的协议: "vless" / "hy2"
window = None
config_ready = False
proxy_process = None        # 统一的代理进程引用 (Xray 或 Hysteria)
lock_file_handle = None
region_label = None
protocol_label = None       # UI: 显示当前协议和延迟

# UDP 阻断惩罚降级机制
penalized_protocol = None
penalty_until = 0

# 尝试引入 Pillow 处理图标
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ================= 基础工具 =================

def resource_path(relative_path):
    """资源路径处理 (适配 PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# 加载语言包
lang_data = {}
def load_language():
    global lang_data
    try:
        with open(resource_path("languages.json"), "r", encoding="utf-8") as f:
            languages = json.load(f)
        lang_env = os.environ.get('LANG', 'en_US')
        if 'zh_CN' in lang_env or 'zh_SG' in lang_env:
            lang_data = languages.get('zh', languages['en'])
        else:
            lang_data = languages.get('en', languages['en'])
    except:
        lang_data = {"app_title": "Xiexie VPN", "messages": {}}

    # 确保协议相关文本存在（兼容未更新的 languages.json）
    if "protocol_label" not in lang_data:
        lang_data["protocol_label"] = "Protocol"
    msgs = lang_data.get("messages", {})
    if "speed_testing" not in msgs:
        msgs["speed_testing"] = "Speed testing..."
    if "speed_test_failed" not in msgs:
        msgs["speed_test_failed"] = "Speed test failed"
    if "degrading" not in msgs:
        msgs["degrading"] = "Network blocked, smart fallback..."
    lang_data["messages"] = msgs

def get_text(key): return lang_data.get(key, key)
def get_message(key): return lang_data.get("messages", {}).get(key, key)

def check_single_instance():
    """防止重复运行"""
    global lock_file_handle
    lock_path = os.path.join(os.path.sep, 'tmp', 'xiexievpn.lock')
    try:
        lock_file_handle = open(lock_path, 'w')
        fcntl.lockf(lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("Already running")
        sys.exit(0)

def check_environment():
    """
    验证运行环境，确保程序能正常工作。
    必须满足：1) 图形会话  2) DBus 会话可用
    注意：root 检查已由 auto_demotion() 在入口点处理
    """
    errors = []
    warnings = []
    
    # 1. 检查 DISPLAY 环境变量（图形会话）
    display = os.environ.get('DISPLAY')
    if not display:
        errors.append(get_message("err_no_display"))
    
    # 2. 检查 DBUS_SESSION_BUS_ADDRESS（gsettings 依赖，改为警告而非致命错误）
    dbus_addr = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    if not dbus_addr:
        # 尝试从文件恢复（某些桌面环境可能未正确导出）
        dbus_files = glob.glob(str(Path.home() / ".dbus" / "session-bus" / "*"))
        if not dbus_files:
            # 改为警告，程序仍可运行（Xray 代理可用，但系统代理设置可能不生效）
            warnings.append(get_message("warn_no_dbus"))
    
    # 3. 检查桌面环境类型（警告，非致命）
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    session = os.environ.get('DESKTOP_SESSION', '').lower()
    
    gnome_like = any(x in desktop or x in session for x in ['gnome', 'unity', 'ubuntu', 'budgie', 'cinnamon'])
    
    if not gnome_like:
        desktop_name = desktop or session or 'Unknown'
        warnings.append(get_message("warn_non_gnome").replace("{desktop}", desktop_name))
    
    # 显示错误（致命）
    if errors:
        # 尝试用 Tkinter 显示，如果失败则用 stderr
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(get_message("env_check_title"), "\n\n".join(errors))
            root.destroy()
        except:
            print("=" * 60, file=sys.stderr)
            print(get_message("env_check_title"), file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            for e in errors:
                print(e, file=sys.stderr)
        sys.exit(1)
    
    # 显示警告（非致命，继续运行）
    if warnings:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(get_message("env_notice_title"), "\n\n".join(warnings))
            root.destroy()
        except:
            for w in warnings:
                print(f"[Warning] {w}", file=sys.stderr)

# ================= 核心：系统代理设置 (Ubuntu 24.04 优化) =================

def set_linux_proxy(enable, host="127.0.0.1", port="1080", socks_port="10809"):
    """
    使用 gsettings 设置 GNOME 代理。
    必须同时设置 HTTP, HTTPS 和 SOCKS 才能保证浏览器正常工作。
    """
    env = os.environ.copy()
    
    try:
        if enable:
            # 1. 设置忽略主机 (非常重要，否则本地服务会挂)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts", "['localhost', '127.0.0.0/8', '::1']"], env=env)
            
            # 2. 设置 HTTP/HTTPS
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)], env=env)
            
            # 3. 设置 SOCKS (关键修复：Ubuntu 24.04 很多应用依赖这个)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", str(socks_port)], env=env)
            
            # 4. 最后启用手动模式
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"], env=env)
        else:
            # 关闭代理
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"], env=env)
            
    except Exception as e:
        print(f"Proxy Error: {e}")

# ================= 代理核心进程管理 (多协议) =================

def ensure_xray_binary():
    """确保 xray 存在并有执行权限"""
    src = resource_path("xray")
    if not XRAY_BIN.exists() or (os.path.exists(src) and os.stat(src).st_size != XRAY_BIN.stat().st_size):
        try: shutil.copy(src, XRAY_BIN)
        except: pass
    
    # 强制赋予 +x 权限 (解决 Permission denied 问题)
    if XRAY_BIN.exists():
        st = os.stat(XRAY_BIN)
        os.chmod(XRAY_BIN, st.st_mode | stat.S_IEXEC)
        
    # 确保 dat 文件存在
    for dat in ["geoip.dat", "geosite.dat"]:
        dat_src = resource_path(dat)
        dat_dst = CONFIG_DIR / dat
        if os.path.exists(dat_src) and not dat_dst.exists():
            try: shutil.copy(dat_src, dat_dst)
            except: pass

def ensure_hy2_binary():
    """确保 hysteria 二进制存在并有执行权限"""
    src = resource_path("hysteria")
    if not HY2_BIN.exists() or (os.path.exists(src) and os.stat(src).st_size != HY2_BIN.stat().st_size):
        try: shutil.copy(src, HY2_BIN)
        except: pass
    
    if HY2_BIN.exists():
        st = os.stat(HY2_BIN)
        os.chmod(HY2_BIN, st.st_mode | stat.S_IEXEC)

def manage_proxy_process(action):
    """统一的代理进程管理：根据 current_protocol 启动对应核心"""
    global proxy_process
    if action == "start":
        manage_proxy_process("stop")
        try:
            if current_protocol == "hy2":
                ensure_hy2_binary()
                if not HY2_BIN.exists():
                    return False
                proxy_process = subprocess.Popen(
                    [str(HY2_BIN), "-c", str(HY2_CONFIG_FILE)],
                    cwd=str(CONFIG_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            else:
                ensure_xray_binary()
                if not XRAY_BIN.exists():
                    return False
                proxy_process = subprocess.Popen(
                    [str(XRAY_BIN), "run", "-c", str(CONFIG_FILE)],
                    cwd=str(CONFIG_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            # 等待确保没有立即崩溃
            time.sleep(0.3)
            if proxy_process.poll() is not None:
                return False
            return True
        except Exception as e:
            print(f"Start proxy failed: {e}")
            return False
    elif action == "stop":
        if proxy_process:
            try: proxy_process.terminate()
            except: pass
            proxy_process = None
        # 干净地杀掉所有残留进程
        subprocess.run(["pkill", "-f", str(XRAY_BIN)], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", str(HY2_BIN)], stderr=subprocess.DEVNULL)
        # 关键：给内核时间回收端口，避免 "Address already in use"
        time.sleep(0.3)

# ================= 自动更新逻辑 =================

def compare_versions(v1, v2):
    try:
        p1 = [int(x) for x in v1.split('.')]
        p2 = [int(x) for x in v2.split('.')]
        return (p1 > p2) - (p1 < p2)
    except: return 0

def update_checker():
    """检查更新并执行覆盖"""
    try:
        # Linux 独立的 version.json
        no_proxy = {"http": None, "https": None}
        r = requests.get("https://xiexievpn.com/cn/linux/version.json", proxies=no_proxy, timeout=5)
        if r.status_code == 200:
            info = r.json()
            latest = info.get("version", "0.0.0")
            if compare_versions(latest, CURRENT_VERSION) > 0:
                if messagebox.askyesno(get_message("update_available"), f"{get_message('optional_update_msg')}\nv{latest}"):
                    perform_update()
    except: pass

def perform_update():
    try:
        # 下载 Linux 二进制文件 (请确保服务器有此文件)
        url = "https://xiexievpn.com/cn/linux/xiexievpn_linux" 
        tmp_path = Path("/tmp/xiexievpn_new")
        
        r = requests.get(url, stream=True)
        with open(tmp_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # 赋予执行权限
        st = os.stat(tmp_path)
        os.chmod(tmp_path, st.st_mode | stat.S_IEXEC)
        
        # 覆盖当前运行的文件 (Linux 特性: 允许覆盖正在运行的二进制)
        current_exe = os.path.abspath(sys.argv[0])
        shutil.move(str(tmp_path), current_exe)
        
        messagebox.showinfo("Update", "Update complete! Restarting...")
        # 重启程序
        os.execv(current_exe, sys.argv)
    except Exception as e:
        messagebox.showerror("Update Error", str(e))

# ================= 核心：多协议测速与配置生成引擎 =================

def test_tcp_ping(host, port=443):
    """对目标 host:443 发起 TCP 握手测延迟（ms）。
    统一测 443 端口评估物理链路，因为 HY2 是纯 UDP 协议，直接 TCP 测其端口会 Connection Refused。
    """
    try:
        st = time.time()
        with socket.create_connection((host, int(port)), timeout=1.5):
            return (time.time() - st) * 1000
    except Exception:
        return float('inf')

def safe_b64decode(data):
    """安全的 Base64 解码，自动补齐 padding"""
    data = data.strip()
    data += "=" * ((4 - len(data) % 4) % 4)
    return base64.b64decode(data).decode('utf-8', errors='ignore')

def speed_test_nodes(links_text):
    """解析链接文本，并行 TCP Ping 测速，返回最优节点"""
    nodes = []
    
    # 兼容 Base64 编码的订阅内容
    if "://" not in links_text:
        try:
            links_text = safe_b64decode(links_text)
        except:
            pass

    for line in links_text.strip().split('\n'):
        line = line.strip()
        if line.startswith("vless://") or line.startswith("hysteria2://") or line.startswith("hy2://"):
            try:
                protocol = "vless" if line.startswith("vless") else "hy2"
                main_part = line.split("://")[1]
                host_port = main_part.split("@")[1].split("?")[0].split("/")[0]
                host = host_port.split(":")[0]
                # 统一对 443 端口测速（评估物理链路延迟）
                nodes.append({"protocol": protocol, "url": line, "host": host, "port": 443})
            except:
                pass
            
    if not nodes:
        return None

    def test_node(node):
        node["ping"] = test_tcp_ping(node["host"], node["port"])
        # UDP 阻断惩罚：如果协议被 Watchdog 判定过阻断，人为增加 5000ms 延迟迫使其降级
        if penalized_protocol == node["protocol"] and time.time() < penalty_until:
            if node["ping"] != float('inf'):
                node["ping"] += 5000
        # HY2 延迟补偿：HY2 抗拥塞更强，减 50ms 增加选中概率
        if node["protocol"] == "hy2" and node["ping"] != float('inf'):
            node["ping"] = max(0, node["ping"] - 50)
        return node
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, max(1, len(nodes)))) as executor:
        results = list(executor.map(test_node, nodes))
        
    valid = [r for r in results if r["ping"] != float('inf')]
    if not valid:
        # 兜底：如果 TCP 全面阻断，盲选第一个节点
        nodes[0]["ping"] = float('inf')
        return nodes[0]
        
    valid.sort(key=lambda x: x["ping"])
    return valid[0]

def write_vless_config(url_string):
    """解析 vless:// URL 并生成 xray config.json"""
    try:
        if not url_string.startswith("vless://"):
            return False
        uuid = url_string.split("@")[0].split("://")[1]
        main_part = url_string.split("@")[1]
        domain_port_part = main_part.split("?")[0]
        domain = domain_port_part.split(":")[0].split(".")[0]
        query_part = url_string.split("?")[1].split("#")[0]
        params = urllib.parse.parse_qs(query_part)
        public_key = params.get('pbk', [''])[0] or "mUzqKeHBc-s1m03iD8Dh1JoL2B9JwG5mMbimEoJ523o"
        short_id = params.get('sid', [''])[0]
        sni = params.get('sni', [f"{domain}.rocketchats.xyz"])[0].replace("www.", "")

        outbounds = [
            {"protocol": "vless", "settings": {"vnext": [{"address": sni, "port": 443, "users": [{"id": uuid, "encryption": "none", "flow": "xtls-rprx-vision"}]}]}, "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "fingerprint": "chrome", "serverName": sni, "publicKey": public_key, "shortId": short_id, "spiderX": ""}}, "tag": "proxy"},
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"}
        ]
        rules = [
            {"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "block"},
            {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
            {"type": "field", "domain": ["geosite:geolocation-!cn"], "outboundTag": "proxy"},
            {"type": "field", "ip": ["geoip:cn", "geoip:private"], "outboundTag": "direct"}
        ]
        config_data = {
            "log": {"loglevel": "none"},
            "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules},
            "inbounds": [
                {"listen": "127.0.0.1", "port": 10809, "protocol": "socks"},
                {"listen": "127.0.0.1", "port": 1080, "protocol": "http"}
            ],
            "outbounds": outbounds
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
        return True
    except Exception as e:
        print(f"VLESS Config Error: {e}")
        return False

def write_hy2_config(url_string):
    """解析 hysteria2:// URL 并生成 hy2_config.json（纯 JSON，0 额外依赖）。
    端口严格对齐 SOCKS5=10809, HTTP=1080，与系统代理设置一致。
    """
    try:
        main_part = url_string.split("://")[1]
        uuid = main_part.split("@")[0]
        host_port = main_part.split("@")[1].split("?")[0].split("/")[0]
        query_part = main_part.split("?")[1].split("#")[0] if "?" in main_part else ""
        sni = urllib.parse.parse_qs(query_part).get('sni', [host_port.split(':')[0]])[0]

        config_data = {
            "server": host_port,
            "auth": uuid,
            "tls": {
                "sni": sni,
                "insecure": False
            },
            "socks5": {
                "listen": "127.0.0.1:10809"
            },
            "http": {
                "listen": "127.0.0.1:1080"
            }
        }
        with open(HY2_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
        return True
    except Exception as e:
        print(f"HY2 Config Error: {e}")
        return False

def parse_and_write_config_async(links_text, callback=None):
    """异步测速 + 写入最优节点配置（线程安全，所有 UI 操作通过 window.after）"""
    global current_protocol, config_ready, pending_autostart
    
    if protocol_label and window:
        window.after(0, lambda: protocol_label.config(text=get_message("speed_testing"), fg="orange"))
        
    def task():
        global current_protocol, config_ready, pending_autostart
        best_node = speed_test_nodes(links_text)
        if not best_node:
            if protocol_label and window:
                window.after(0, lambda: protocol_label.config(text=get_message("speed_test_failed"), fg="red"))
            if callback and window:
                window.after(0, lambda: callback(False))
            return
            
        if best_node["protocol"] == "vless":
            success = write_vless_config(best_node["url"])
        else:
            success = write_hy2_config(best_node["url"])
            
        if success:
            current_protocol = best_node["protocol"]
            config_ready = True
            
            def update_ui():
                global pending_autostart
                if protocol_label:
                    p_text = "VLESS ⚡" if current_protocol == "vless" else "HY2 🚀"
                    ping_text = f"{int(best_node['ping'])}ms" if best_node['ping'] != float('inf') else "Blind"
                    # 显示降级状态
                    if penalized_protocol and time.time() < penalty_until and current_protocol != penalized_protocol:
                        p_text += " (↓ fallback)"
                    protocol_label.config(text=f"{get_text('protocol_label')}: {p_text} ({ping_text})", fg="green")
                
                if pending_autostart:
                    pending_autostart = False
                    set_general_proxy()
                elif proxy_state == 1:
                    # 代理开启状态下静默重载进程
                    manage_proxy_process("stop")
                    manage_proxy_process("start")
                elif 'btn_general_proxy' in globals() and btn_general_proxy and proxy_state == 0:
                    btn_general_proxy.config(state="normal")
                
                if callback:
                    callback(True)
                    
            if window:
                window.after(0, update_ui)
        else:
            if callback and window:
                window.after(0, lambda: callback(False))
                
    threading.Thread(target=task, daemon=True).start()

# ================= 业务逻辑 =================

def update_region_display(zone):
    if window and region_label:
        flag = REGION_TO_FLAG.get(zone, zone)
        # 获取语言包中的地区名
        reg_name = get_message(f"region_{flag}")
        if not reg_name or reg_name == f"region_{flag}":
             reg_name = zone
        
        txt = f"{get_message('current_region')}: {reg_name}"
        region_label.config(text=txt)

def do_adduser(uuid):
    """触发服务端为新用户创建节点"""
    no_proxy = {"http": None, "https": None}
    try:
        requests.post("https://vvv.xiexievpn.com/adduser", json={"code": uuid}, timeout=5, proxies=no_proxy)
    except:
        pass

def fetch_subscription(uuid):
    """双通道配置拉取：优先 Worker 订阅，回退 /getuserinfo"""
    global current_region
    no_proxy = {"http": None, "https": None}
    
    def task():
        global current_region
        links_text = ""
        
        # 通道 1：Cloudflare Worker 订阅
        try:
            resp = requests.get(f"https://{SUB_DOMAIN}/sub/{uuid}?t={int(time.time())}", timeout=5, proxies=no_proxy)
            if resp.status_code == 200:
                links_text = resp.text.strip()
        except:
            pass
        
        # 通道 2：原生 /getuserinfo 兜底
        try:
            response = requests.post("https://vvv.xiexievpn.com/getuserinfo",
                                     json={"code": uuid}, proxies=no_proxy, timeout=10)
            if response.status_code == 200:
                data = response.json()
                zone = data.get("zone", "")
                v2rayurl = data.get("v2rayurl", "")
                
                if zone:
                    current_region = REGION_TO_FLAG.get(zone, zone)
                    if window:
                        window.after(0, lambda: update_region_display(zone))
                
                if not links_text and v2rayurl:
                    links_text = v2rayurl
                
                # 新用户：无配置也无区域，需先 adduser
                if not v2rayurl and not zone:
                    try:
                        requests.post("https://vvv.xiexievpn.com/adduser",
                                      json={"code": uuid}, timeout=2, proxies=no_proxy)
                    except:
                        pass
                    if window:
                        window.after(3000, lambda: fetch_subscription(uuid))
                    return
                
                # 有区域但无链接：等待 VM 创建完成
                if not v2rayurl and zone:
                    if window:
                        window.after(3000, lambda: fetch_subscription(uuid))
                    return
        except:
            pass
            
        if links_text:
            parse_and_write_config_async(links_text)
        else:
            # 都失败了，继续轮询
            if window:
                window.after(3000, lambda: fetch_subscription(uuid))
                
    threading.Thread(target=task, daemon=True).start()

# ================= 自愈机制 (Watchdog) =================

def connection_watchdog(uuid):
    """检测连接。如果代理开启但无法上网，尝试重连。
    增加 UDP 阻断检测：如果使用 HY2 连续失败，设定惩罚期迫使下次测速降级到 VLESS。
    """
    global penalized_protocol, penalty_until
    fail_count = 0
    while True:
        time.sleep(15)
        if proxy_state == 1:
            # 1. 检查代理是否通
            try:
                proxies = {'http': 'http://127.0.0.1:1080', 'https': 'http://127.0.0.1:1080'}
                if requests.get("http://www.google.com/generate_204", proxies=proxies, timeout=5).status_code == 204:
                    fail_count = 0
                    continue
            except: pass
            
            fail_count += 1
            if fail_count >= 2:
                # 2. 检查本地网络
                try:
                    requests.get("https://www.baidu.com", proxies={"http": None, "https": None}, timeout=5)
                    # 本地有网，代理挂了
                    print("Watchdog: Proxy down, refreshing config...")
                    
                    # 如果当前用的是 HY2 且连续失败，大概率 UDP 被阻断
                    if current_protocol == "hy2":
                        penalized_protocol = "hy2"
                        penalty_until = time.time() + 300  # 惩罚 5 分钟
                        print("Watchdog: HY2 penalized for 5 min (UDP likely blocked)")
                    
                    fetch_subscription(uuid)
                    fail_count = 0
                except: pass

# ================= UI 功能 =================

def set_general_proxy():
    global proxy_state
    # 检查对应协议的配置文件是否存在
    config_exists = False
    if current_protocol == "hy2":
        config_exists = HY2_CONFIG_FILE.exists()
    else:
        config_exists = CONFIG_FILE.exists()
    
    if not config_exists:
        messagebox.showinfo(get_text("app_title"), get_message("config_preparing"))
        return
    
    if manage_proxy_process("start"):
        set_linux_proxy(True)
        messagebox.showinfo("Information", get_message("vpn_setup_success"))
        btn_general_proxy.config(state="disabled")
        btn_close_proxy.config(state="normal")
        proxy_state = 1
        toggle_autostart()
    else:
        messagebox.showerror("Error", "Failed to start proxy core.")

def close_proxy():
    global proxy_state
    set_linux_proxy(False)
    manage_proxy_process("stop")
    messagebox.showinfo("Information", get_message("vpn_closed"))
    btn_close_proxy.config(state="disabled")
    btn_general_proxy.config(state="normal")
    proxy_state = 0
    toggle_autostart()

def toggle_autostart():
    autostart_dir = Path.home() / ".config" / "autostart"
    desktop_file = autostart_dir / "xiexievpn.desktop"
    if chk_autostart.get():
        autostart_dir.mkdir(parents=True, exist_ok=True)
        exe_path = os.path.abspath(sys.argv[0])
        content = f"""[Desktop Entry]\nType=Application\nExec={exe_path} 1\nHidden=false\nX-GNOME-Autostart-enabled=true\nName=XiexieVPN\n"""
        with open(desktop_file, "w") as f: f.write(content)
    else:
        if desktop_file.exists(): desktop_file.unlink()
    with open(AUTOSTART_FILE, "w") as f: f.write("1" if chk_autostart.get() else "0")

def on_closing():
    if proxy_state == 1:
        set_linux_proxy(False)
        manage_proxy_process("stop")
    window.destroy()
    sys.exit(0)

# ================= 辅助 UI =================

def create_context_menu(widget):
    """创建右键菜单"""
    menu = Menu(widget, tearoff=0)
    menu.add_command(label=get_text("copy"), command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label=get_text("paste"), command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_command(label=get_text("select_all"), command=lambda: widget.select_range(0, 'end'))
    
    def popup(e):
        menu.post(e.x_root, e.y_root)
    
    # Linux 使用 Button-3
    widget.bind("<Button-3>", popup)
    # 绑定 Ctrl+V, Ctrl+A
    widget.bind("<Control-v>", lambda e: widget.event_generate("<<Paste>>"))
    widget.bind("<Control-a>", lambda e: widget.select_range(0, 'end'))

# ================= 窗口逻辑 =================

def show_main_window(uuid):
    global window, btn_general_proxy, btn_close_proxy, chk_autostart, region_label, protocol_label
    window = tk.Tk()
    window.title(get_text("app_title"))
    window.geometry("420x420")
    
    # 图标处理
    try:
        if PIL_AVAILABLE:
            ico_path = resource_path("favicon.ico")
            if os.path.exists(ico_path):
                img = ImageTk.PhotoImage(Image.open(ico_path))
                window.iconphoto(True, img)
                window.image = img
    except: pass
    
    window.protocol("WM_DELETE_WINDOW", on_closing)

    btn_general_proxy = tk.Button(window, text=get_text("open_vpn"), command=set_general_proxy, pady=5)
    btn_general_proxy.pack(pady=15, fill='x', padx=30)
    
    btn_close_proxy = tk.Button(window, text=get_text("close_vpn"), command=close_proxy, pady=5, state="disabled")
    btn_close_proxy.pack(pady=5, fill='x', padx=30)
    
    chk_autostart = tk.BooleanVar()
    if AUTOSTART_FILE.exists():
        with open(AUTOSTART_FILE, "r") as f: chk_autostart.set(f.read().strip() == "1")
    tk.Checkbutton(window, text=get_text("autostart"), variable=chk_autostart, command=toggle_autostart).pack(pady=10)

    # 协议状态显示
    protocol_label = tk.Label(window, text=get_message("speed_testing"), fg="orange")
    protocol_label.pack(pady=5)

    # 区域显示
    region_label = tk.Label(window, text=f"{get_message('current_region')}: ...", fg="gray")
    region_label.pack(side="bottom", pady=15)

    # 使用双通道订阅拉取（Worker + getuserinfo）
    fetch_subscription(uuid)
    threading.Thread(target=update_checker, daemon=True).start()
    threading.Thread(target=connection_watchdog, args=(uuid,), daemon=True).start()

    if len(sys.argv) > 1 and sys.argv[1] == "1":
        global pending_autostart
        pending_autostart = True
        window.iconify()
    window.mainloop()

if __name__ == "__main__":
    auto_demotion()  # 如果以 root 运行，自动降权到桌面用户
    check_single_instance()
    load_language()  # 先加载语言包，以便环境检查能使用国际化文本
    check_environment()  # 验证运行环境（桌面会话、DBus）
    
    login_window = tk.Tk()
    login_window.title(get_text("login_title"))
    login_window.geometry("300x200")
    
    try:
        if PIL_AVAILABLE:
            ico_path = resource_path("favicon.ico")
            if os.path.exists(ico_path):
                img = ImageTk.PhotoImage(Image.open(ico_path))
                login_window.iconphoto(True, img)
    except: pass

    tk.Label(login_window, text=get_text("login_prompt")).pack(pady=10)
    entry_uuid = tk.Entry(login_window)
    entry_uuid.pack(pady=5)
    
    # 绑定右键菜单
    create_context_menu(entry_uuid)
    
    saved_uuid = ""
    if UUID_FILE.exists():
        with open(UUID_FILE, "r") as f: saved_uuid = f.read().strip()
        entry_uuid.insert(0, saved_uuid)
    
    # 检查是否启用了自动登录
    auto_login_enabled = AUTO_LOGIN_FILE.exists()
    
    def do_login():
        u = entry_uuid.get().strip()
        if len(u) > 5:
            # 增加服务端校验，拒绝无效的随机码
            no_proxy = {"http": None, "https": None}
            try:
                response = requests.post("https://vvv.xiexievpn.com/login", json={"code": u}, proxies=no_proxy, timeout=10)
                if response.status_code == 200:
                    with open(UUID_FILE, "w") as f: f.write(u)
                    if chk_remember.get():
                        with open(AUTO_LOGIN_FILE, "w") as f: f.write("1")
                    else:
                        if AUTO_LOGIN_FILE.exists(): AUTO_LOGIN_FILE.unlink()
                    login_window.destroy()
                    show_main_window(u)
                else:
                    msg = get_message("invalid_code") if response.status_code == 401 else get_message("expired") if response.status_code == 403 else get_message("server_error")
                    messagebox.showerror("Error", msg)
            except Exception as e:
                messagebox.showerror("Error", f"{get_message('connection_error')}: {e}")
        else:
            messagebox.showerror("Error", get_message("invalid_code"))

    # 如果已启用自动登录且已有保存的 UUID，直接进入主界面
    if auto_login_enabled and len(saved_uuid) > 5:
        login_window.destroy()
        show_main_window(saved_uuid)
    else:
        chk_remember = tk.BooleanVar(value=auto_login_enabled)  # 默认与之前的设置一致
        tk.Checkbutton(login_window, text=get_text("auto_login"), variable=chk_remember).pack()
        tk.Button(login_window, text=get_text("login_button"), command=do_login).pack(pady=10)
        
        login_window.mainloop()
