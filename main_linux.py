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
CURRENT_VERSION = "1.0.9" 
APP_NAME = "xiexievpn"

# Linux 配置路径遵循 XDG 标准: ~/.config/xiexievpn
CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "config.json"
UUID_FILE = CONFIG_DIR / "uuid.txt"
AUTOSTART_FILE = CONFIG_DIR / "autostart_state.txt"
AUTO_LOGIN_FILE = CONFIG_DIR / "auto_login.txt"  # 标记是否自动登录
XRAY_BIN = CONFIG_DIR / "xray"

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
window = None
config_ready = False
xray_process = None
lock_file_handle = None
region_label = None

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

# ================= Xray 进程管理 =================

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

def manage_xray(action):
    global xray_process
    if action == "start":
        manage_xray("stop")
        ensure_xray_binary()
        try:
            xray_process = subprocess.Popen(
                [str(XRAY_BIN), "run", "-c", str(CONFIG_FILE)],
                cwd=str(CONFIG_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            # 等待一小会儿确保没有立即崩溃
            time.sleep(0.2)
            if xray_process.poll() is not None:
                return False
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Start Xray Failed: {e}")
            return False
    elif action == "stop":
        if xray_process:
            xray_process.terminate()
            xray_process = None
        subprocess.run(["pkill", "-f", f"{XRAY_BIN} run -c"], stderr=subprocess.DEVNULL)

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

# ================= 业务逻辑 =================

def parse_and_write_config(url_string):
    global config_ready, pending_autostart, proxy_state
    try:
        if not url_string.startswith("vless://"): return
        
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
            {"protocol": "vless", "settings": {"vnext": [{"address": f"{domain}.rocketchats.xyz", "port": 443, "users": [{"id": uuid, "encryption": "none", "flow": "xtls-rprx-vision"}]}]}, "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"show": False, "fingerprint": "chrome", "serverName": sni, "publicKey": public_key, "shortId": short_id, "spiderX": ""}}, "tag": "proxy"},
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
            "inbounds": [{"listen": "127.0.0.1", "port": 10809, "protocol": "socks"}, {"listen": "127.0.0.1", "port": 1080, "protocol": "http"}],
            "outbounds": outbounds
        }
        
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)

        config_ready = True
        
        # 如果是重连，重启服务
        if proxy_state == 1:
            manage_xray("start")

        if pending_autostart:
            pending_autostart = False
            set_general_proxy()
            
    except Exception as e:
        print(f"Config Error: {e}")

def update_region_display(zone):
    if window and region_label:
        flag = REGION_TO_FLAG.get(zone, zone)
        # 获取语言包中的地区名
        reg_name = get_message(f"region_{flag}")
        if not reg_name or reg_name == f"region_{flag}":
             reg_name = zone
        
        txt = f"{get_text('current_region')}: {reg_name}"
        region_label.config(text=txt)

def fetch_config_data(uuid):
    try:
        no_proxy = {"http": None, "https": None}
        res = requests.post("https://vvv.xiexievpn.com/getuserinfo", json={"code": uuid}, proxies=no_proxy, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("zone"):
                window.after(0, lambda: update_region_display(data["zone"]))
            if data.get("v2rayurl"):
                parse_and_write_config(data["v2rayurl"])
    except: pass

# ================= 自愈机制 (Watchdog) =================

def connection_watchdog(uuid):
    """检测连接，如果代理开启但无法上网，尝试重连"""
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
                    # 本地有网，代理挂了 -> 重连
                    print("Watchdog: Refreshing config...")
                    fetch_config_data(uuid)
                    fail_count = 0
                except: pass

# ================= UI 功能 =================

def set_general_proxy():
    global proxy_state
    if not CONFIG_FILE.exists():
        messagebox.showinfo(get_text("app_title"), get_message("config_preparing"))
        return
    
    if manage_xray("start"):
        set_linux_proxy(True)
        messagebox.showinfo("Information", get_message("vpn_setup_success"))
        btn_general_proxy.config(state="disabled")
        btn_close_proxy.config(state="normal")
        proxy_state = 1
        toggle_autostart()
    else:
        messagebox.showerror("Error", "Failed to start Xray core.")

def close_proxy():
    global proxy_state
    set_linux_proxy(False)
    manage_xray("stop")
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
        manage_xray("stop")
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
    global window, btn_general_proxy, btn_close_proxy, chk_autostart, region_label
    window = tk.Tk()
    window.title(get_text("app_title"))
    window.geometry("300x380")
    
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

    # 区域显示
    region_label = tk.Label(window, text=f"{get_text('current_region')}: ...", fg="gray")
    region_label.pack(side="bottom", pady=15)

    threading.Thread(target=fetch_config_data, args=(uuid,), daemon=True).start()
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
            # 保存 UUID
            with open(UUID_FILE, "w") as f: f.write(u)
            # 根据复选框状态决定是否创建自动登录标记文件
            if chk_remember.get():
                with open(AUTO_LOGIN_FILE, "w") as f: f.write("1")
            else:
                if AUTO_LOGIN_FILE.exists(): AUTO_LOGIN_FILE.unlink()
            login_window.destroy()
            show_main_window(u)
        else: messagebox.showerror("Error", get_message("invalid_code"))

    # 如果已启用自动登录且已有保存的 UUID，直接进入主界面
    if auto_login_enabled and len(saved_uuid) > 5:
        login_window.destroy()
        show_main_window(saved_uuid)
    else:
        chk_remember = tk.BooleanVar(value=auto_login_enabled)  # 默认与之前的设置一致
        tk.Checkbutton(login_window, text=get_text("auto_login"), variable=chk_remember).pack()
        tk.Button(login_window, text=get_text("login_button"), command=do_login).pack(pady=10)
        
        login_window.mainloop()
