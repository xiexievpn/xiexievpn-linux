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
import random
import tempfile
from pathlib import Path
import socks                   
import urllib3.contrib.socks    

def detect_desktop_user():
    """
    检测控制当前 GNOME 桌面会话的原始用户。
    返回用户名，如果无法检测则返回 None。
    """
    
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return sudo_user

    for var in ['LOGNAME', 'USER']:
        user = os.environ.get(var)
        if user and user != 'root':
            return user

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

    display = os.environ.get('DISPLAY', ':0')
    display_num = display.replace(':', '').split('.')[0] or '0'
    try:
        
        x_socket = f'/tmp/.X11-unix/X{display_num}'
        if os.path.exists(x_socket):
            import pwd
            uid = os.stat(x_socket).st_uid
            user = pwd.getpwuid(uid).pw_name
            if user != 'root':
                return user
    except:
        pass

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
        return  
    
    target_user = detect_desktop_user()
    if not target_user:
        
        print("错误: 以 root 身份运行，但无法检测到桌面用户。", file=sys.stderr)
        print("Error: Running as root, but cannot detect desktop user.", file=sys.stderr)
        print("请使用普通用户运行此程序。", file=sys.stderr)
        sys.exit(1)
    
    print(f"检测到桌面用户: {target_user}")
    print(f"正在以 {target_user} 身份重新启动...")

    display = os.environ.get('DISPLAY', ':0')

    subprocess.run(['xhost', f'+SI:localuser:{target_user}'], 
                   stderr=subprocess.DEVNULL, timeout=5)
    subprocess.run(['xhost', '+local:'], 
                   stderr=subprocess.DEVNULL, timeout=5)

    if getattr(sys, 'frozen', False):
        
        program = sys.executable
        program_args = sys.argv[1:]  
    else:
        
        program = f"python3 {os.path.abspath(__file__)}"
        program_args = sys.argv[1:]

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

    os.execlp('su', 'su', '-', target_user, '-c', inner_cmd)

CURRENT_VERSION = "2.1.2" 
APP_NAME = "xiexievpn"
SUB_DOMAIN = "sub.xiexievpn.com"
API_ENDPOINTS = [
    "https://payapi.xiexievpn.com",
    "https://vvv.xiexievpn.com"
]

CONFIG_DIR = Path.home() / ".config" / APP_NAME
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = CONFIG_DIR / "config.json"       
HY2_CONFIG_FILE = CONFIG_DIR / "hy2_config.json" 
UUID_FILE = CONFIG_DIR / "uuid.txt"
AUTOSTART_FILE = CONFIG_DIR / "autostart_state.txt"
AUTO_LOGIN_FILE = CONFIG_DIR / "auto_login.txt"  
XRAY_BIN = CONFIG_DIR / "xray"
HY2_BIN = CONFIG_DIR / "hysteria"

REGION_TO_FLAG = {
    "us-west-2": "us", "ap-northeast-2": "jp", "ap-northeast-1": "jj",
    "ap-southeast-1": "si", "ap-southeast-2": "au", "ap-south-1": "in",
    "ca-central-1": "ca", "eu-central-1": "ge", "eu-west-1": "ir",
    "eu-west-2": "ki", "eu-west-3": "fr", "eu-north-1": "sw"
}

proxy_state = 0            
pending_autostart = False
current_region = None
current_uuid = None
current_protocol = None     # 当前使用的协议: "vless" / "hy2"
window = None
config_ready = False
proxy_process = None        
lock_file_handle = None
region_label = None
protocol_label = None       

penalized_protocol = None
penalty_until = 0
current_node_url = None  

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def resource_path(relative_path):
    """资源路径处理 (适配 PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

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

    display = os.environ.get('DISPLAY')
    if not display:
        errors.append(get_message("err_no_display"))

    dbus_addr = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    if not dbus_addr:
        
        dbus_files = glob.glob(str(Path.home() / ".dbus" / "session-bus" / "*"))
        if not dbus_files:
            
            warnings.append(get_message("warn_no_dbus"))

    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    session = os.environ.get('DESKTOP_SESSION', '').lower()
    
    gnome_like = any(x in desktop or x in session for x in ['gnome', 'unity', 'ubuntu', 'budgie', 'cinnamon'])
    
    if not gnome_like:
        desktop_name = desktop or session or 'Unknown'
        warnings.append(get_message("warn_non_gnome").replace("{desktop}", desktop_name))

    if errors:
        
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

def set_linux_proxy(enable, host="127.0.0.1", port="1080", socks_port="10809"):
    """
    使用 gsettings 设置 GNOME 代理。
    必须同时设置 HTTP, HTTPS 和 SOCKS 才能保证浏览器正常工作。
    """
    env = os.environ.copy()
    
    try:
        if enable:
            
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts", "['localhost', '127.0.0.0/8', '::1']"], env=env)

            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", str(port)], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", str(port)], env=env)

            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", host], env=env)
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", str(socks_port)], env=env)

            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"], env=env)
        else:
            
            subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"], env=env)
            
    except Exception as e:
        print(f"Proxy Error: {e}")

def ensure_xray_binary():
    """确保 xray 存在并有执行权限"""
    src = resource_path("xray")
    if not XRAY_BIN.exists() or (os.path.exists(src) and os.stat(src).st_size != XRAY_BIN.stat().st_size):
        try: shutil.copy(src, XRAY_BIN)
        except: pass

    if XRAY_BIN.exists():
        st = os.stat(XRAY_BIN)
        os.chmod(XRAY_BIN, st.st_mode | stat.S_IEXEC)

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
        
        subprocess.run(["pkill", "-f", str(XRAY_BIN)], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", str(HY2_BIN)], stderr=subprocess.DEVNULL)
        # 关键：给内核时间回收端口，避免 "Address already in use"
        time.sleep(0.3)

def compare_versions(v1, v2):
    try:
        p1 = [int(x) for x in v1.split('.')]
        p2 = [int(x) for x in v2.split('.')]
        return (p1 > p2) - (p1 < p2)
    except: return 0

def update_checker():
    """检查更新并执行强制覆盖更新"""
    try:
        
        no_proxy = {"http": None, "https": None}
        r = requests.get("https://xiexievpn.com/cn/linux/version.json", proxies=no_proxy, timeout=5)
        if r.status_code == 200:
            info = r.json()
            latest = info.get("version", "0.0.0")
            notes = info.get("releaseNotes", "")
            if compare_versions(latest, CURRENT_VERSION) > 0:
                def _do_force_update():
                    notes_str = f"\n\n{notes}" if notes else ""
                    messagebox.showinfo(
                        get_message("update_required"),
                        f"{get_message('force_update_msg')}\n\nv{latest}{notes_str}"
                    )
                    perform_update()

                if window:
                    window.after(0, _do_force_update)
                else:
                    _do_force_update()
    except: pass

def perform_update():
    try:
        
        url = "https://xiexievpn.com/cn/linux/xiexievpn_linux" 
        tmp_path = Path("/tmp/xiexievpn_new")
        no_proxy = {"http": None, "https": None}
        
        r = requests.get(url, stream=True, proxies=no_proxy, timeout=60)
        r.raise_for_status()
        with open(tmp_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        st = os.stat(tmp_path)
        os.chmod(tmp_path, st.st_mode | stat.S_IEXEC)

        current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
        shutil.move(str(tmp_path), current_exe)
        
        messagebox.showinfo("Update", "Update complete! Restarting...")
        
        os.execv(current_exe, [current_exe] + sys.argv[1:])
    except Exception as e:
        messagebox.showerror("Update Error", str(e))

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

def test_hy2_url_test(node):
    """启动临时 Hysteria 进程，通过 SOCKS5 代理做 URL Test 测真实延迟"""
    temp_port = random.randint(30000, 39999)
    config = {
        "server": f"{node['host']}:{node['port']}",
        "auth": node.get('uuid', ''),
        "tls": {"sni": node.get('sni', node['host']), "insecure": False},
        "socks5": {"listen": f"127.0.0.1:{temp_port}"}
    }
    config_path = os.path.join(tempfile.gettempdir(), f"hy2_test_{temp_port}.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f)

    proc = None
    try:
        ensure_hy2_binary()
        if not HY2_BIN.exists():
            return float('inf')
        proc = subprocess.Popen(
            [str(HY2_BIN), "client", "-c", config_path],
            cwd=str(CONFIG_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        if proc.poll() is not None:
            return float('inf')

        proxies = {'http': f'socks5h://127.0.0.1:{temp_port}',
                   'https': f'socks5h://127.0.0.1:{temp_port}'}
        st = time.time()
        resp = requests.get("http://cp.cloudflare.com/generate_204",
                           proxies=proxies, timeout=5)
        if resp.status_code == 204:
            return (time.time() - st) * 1000
        return float('inf')
    except Exception:
        return float('inf')
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        try:
            os.remove(config_path)
        except Exception:
            pass

def safe_b64decode(data):
    """安全的 Base64 解码，自动补齐 padding"""
    data = data.strip()
    data += "=" * ((4 - len(data) % 4) % 4)
    return base64.b64decode(data).decode('utf-8', errors='ignore')

def extract_region_from_link(link):
    """从节点 URL 的 #fragment 中提取区域标记（如 us/si 或 aws region）。"""
    try:
        if "#" not in link:
            return None

        frag = urllib.parse.unquote(link.split("#", 1)[1]).strip().lower()
        if not frag:
            return None

        if frag.endswith("-hy2"):
            frag = frag[:-4]

        if frag in REGION_TO_FLAG.values():
            return frag

        if frag in REGION_TO_FLAG:
            return REGION_TO_FLAG[frag]

        for aws_region, flag_code in REGION_TO_FLAG.items():
            if frag.startswith(aws_region):
                return flag_code

        return None
    except Exception:
        return None

def speed_test_nodes(links_text):
    """解析链接文本，并行 TCP Ping 测速，返回最优节点"""
    nodes = []

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
                host_parts = host_port.split(":")
                host = host_parts[0]
                port = int(host_parts[1]) if len(host_parts) > 1 else 443
                node_info = {"protocol": protocol, "url": line, "host": host, "port": port}
                
                if protocol == "hy2":
                    node_info["uuid"] = main_part.split("@")[0]
                    query_part = main_part.split("?")[1].split("#")[0] if "?" in main_part else ""
                    node_info["sni"] = urllib.parse.parse_qs(query_part).get('sni', [host])[0]
                nodes.append(node_info)
            except:
                pass
            
    if not nodes:
        return None

    def test_node(node):
        if node["protocol"] == "hy2":
            node["ping"] = test_hy2_url_test(node)  
        else:
            tcp_ping = test_tcp_ping(node["host"], 443)
            
            node["ping"] = tcp_ping * 3 + 100 if tcp_ping != float('inf') else float('inf')
        
        if penalized_protocol == node["protocol"] and time.time() < penalty_until:
            if node["ping"] != float('inf'):
                node["ping"] += 5000
        return node
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, max(1, len(nodes)))) as executor:
        results = list(executor.map(test_node, nodes))
        
    valid = [r for r in results if r["ping"] != float('inf')]
    if not valid:
        
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

def parse_and_write_config_async(links_text, callback=None, force_restart=False):
    """异步测速 + 写入最优节点配置（线程安全，所有 UI 操作通过 window.after）"""
    global current_protocol, config_ready, pending_autostart
    
    if protocol_label and window:
        window.after(0, lambda: protocol_label.config(text=get_message("speed_testing"), fg="orange"))
        
    def task():
        global current_protocol, config_ready, pending_autostart, current_node_url, current_region
        best_node = speed_test_nodes(links_text)
        if not best_node:
            if protocol_label and window:
                window.after(0, lambda: protocol_label.config(text=get_message("speed_test_failed"), fg="red"))
            if callback and window:
                window.after(0, lambda: callback(False))
            return

        if not force_restart and best_node["url"] == current_node_url and config_ready:
            selected_region = extract_region_from_link(best_node["url"])
            if selected_region:
                current_region = selected_region
                if window:
                    window.after(0, lambda r=selected_region: update_region_display(r))
            if callback and window:
                window.after(0, lambda: callback(True))
            return
            
        if best_node["protocol"] == "vless":
            success = write_vless_config(best_node["url"])
        else:
            success = write_hy2_config(best_node["url"])
            
        if success:
            current_protocol = best_node["protocol"]
            config_ready = True
            current_node_url = best_node["url"]  
            selected_region = extract_region_from_link(best_node["url"])
            if selected_region:
                current_region = selected_region
            
            def update_ui():
                global pending_autostart
                if protocol_label:
                    p_text = "VLESS ⚡" if current_protocol == "vless" else "HY2 🚀"
                    ping_text = f"{int(best_node['ping'])}ms" if best_node['ping'] != float('inf') else "Blind"
                    
                    if penalized_protocol and time.time() < penalty_until and current_protocol != penalized_protocol:
                        p_text += " (↓ fallback)"
                    protocol_label.config(text=f"{get_text('protocol_label')}: {p_text} ({ping_text})", fg="green")

                if current_region:
                    update_region_display(current_region)
                
                if pending_autostart:
                    pending_autostart = False
                    set_general_proxy()
                elif proxy_state == 1:
                    
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

def update_region_display(zone):
    if window and region_label:
        flag = REGION_TO_FLAG.get(zone, zone)
        
        reg_name = get_message(f"region_{flag}")
        if not reg_name or reg_name == f"region_{flag}":
             reg_name = zone
        
        txt = f"{get_message('current_region')}: {reg_name}"
        region_label.config(text=txt)

def post_api_with_failover(path, payload, timeouts=(7, 10), stop_statuses=(), treat_read_timeout_as_sent=False):
    no_proxy = {"http": None, "https": None}
    last_err = None
    last_resp = None
    for idx, base_url in enumerate(API_ENDPOINTS):
        timeout_val = timeouts[idx] if idx < len(timeouts) else timeouts[-1]
        try:
            resp = requests.post(
                f"{base_url}{path}",
                json=payload,
                headers={"Content-Type": "application/json"},
                proxies=no_proxy,
                timeout=timeout_val
            )
            if (200 <= resp.status_code < 300) or (400 <= resp.status_code < 500) or (resp.status_code in stop_statuses):
                return resp
            last_resp = resp
        except requests.exceptions.ReadTimeout as e:
            if treat_read_timeout_as_sent:
                return None
            last_err = e
        except Exception as e:
            last_err = e
    if last_resp is not None:
        return last_resp
    raise last_err if last_err else RuntimeError("Network error")

def do_adduser(uuid):
    """触发服务端为新用户创建节点"""
    try:
        post_api_with_failover(
            "/adduser",
            {"code": uuid},
            timeouts=((3, 5), (3, 5)),
            treat_read_timeout_as_sent=True
        )
    except:
        pass

def fetch_subscription(uuid, from_watchdog=False):
    """双通道配置拉取：优先 Worker 订阅，回退 /getuserinfo"""
    global current_region
    no_proxy = {"http": None, "https": None}
    
    def task():
        global current_region, current_protocol, penalized_protocol, penalty_until
        links_text = ""
        prev_region = current_region
        region_changed = False

        try:
            resp = requests.get(f"https://{SUB_DOMAIN}/sub/{uuid}?t={int(time.time())}", timeout=5, proxies=no_proxy)
            if resp.status_code == 200:
                links_text = resp.text.strip()
        except:
            pass

        try:
            response = post_api_with_failover("/getuserinfo", {"code": uuid}, timeouts=(7, 10))
            if response.status_code == 200:
                data = response.json()
                zone = data.get("zone", "")
                v2rayurl = data.get("v2rayurl", "")
                
                if zone:
                    mapped_region = REGION_TO_FLAG.get(zone, zone)
                    if prev_region and prev_region != mapped_region:
                        region_changed = True
                    current_region = mapped_region
                    if window:
                        window.after(0, lambda r=mapped_region: update_region_display(r))
                
                if not links_text and v2rayurl:
                    links_text = v2rayurl

                if not v2rayurl and not zone:
                    try:
                        post_api_with_failover(
                            "/adduser",
                            {"code": uuid},
                            timeouts=((2, 2), (2, 2)),
                            treat_read_timeout_as_sent=True
                        )
                    except:
                        pass
                    if window:
                        window.after(3000, lambda: fetch_subscription(uuid, from_watchdog=from_watchdog))
                    return

                if not v2rayurl and zone:
                    if window:
                        window.after(3000, lambda: fetch_subscription(uuid, from_watchdog=from_watchdog))
                    return
        except:
            pass
            
        if links_text:
            if from_watchdog:
                if region_changed:
                    penalized_protocol = None
                    penalty_until = 0
                else:
                    penalized_protocol = current_protocol
                    penalty_until = time.time() + 300  
                    if protocol_label and window:
                        window.after(0, lambda: protocol_label.config(text=get_message("degrading"), fg="red"))
            parse_and_write_config_async(links_text, force_restart=from_watchdog)
        else:
            
            if window:
                window.after(3000, lambda: fetch_subscription(uuid, from_watchdog=from_watchdog))
                
    threading.Thread(target=task, daemon=True).start()

def connection_watchdog(uuid):
    """带协议惩罚降级的连接看门狗（极速复查版）"""
    global penalized_protocol, penalty_until
    while True:
        time.sleep(10)
        if proxy_state == 1:
            
            try:
                proxies = {'http': 'http://127.0.0.1:1080', 'https': 'http://127.0.0.1:1080'}
                if requests.get("http://cp.cloudflare.com/generate_204", proxies=proxies, timeout=5).status_code == 204:
                    continue
            except: pass

            time.sleep(1.5)
            try:
                proxies = {'http': 'http://127.0.0.1:1080', 'https': 'http://127.0.0.1:1080'}
                if requests.get("http://cp.cloudflare.com/generate_204", proxies=proxies, timeout=5).status_code == 204:
                    continue
            except: pass

            try:
                requests.get("https://www.baidu.com", proxies={"http": None, "https": None}, timeout=5)
                
                print("Watchdog: Proxy down, refreshing config...")

                fetch_subscription(uuid, from_watchdog=True)
                time.sleep(15)  
            except: pass

def set_general_proxy():
    global proxy_state
    
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

def create_context_menu(widget):
    """创建右键菜单"""
    menu = Menu(widget, tearoff=0)
    menu.add_command(label=get_text("copy"), command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label=get_text("paste"), command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_command(label=get_text("select_all"), command=lambda: widget.select_range(0, 'end'))
    
    def popup(e):
        menu.post(e.x_root, e.y_root)

    widget.bind("<Button-3>", popup)
    
    widget.bind("<Control-v>", lambda e: widget.event_generate("<<Paste>>"))
    widget.bind("<Control-a>", lambda e: widget.select_range(0, 'end'))

def show_main_window(uuid):
    global window, btn_general_proxy, btn_close_proxy, chk_autostart, region_label, protocol_label
    window = tk.Tk()
    window.title(get_text("app_title"))
    window.geometry("420x420")

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

    protocol_label = tk.Label(window, text=get_message("speed_testing"), fg="orange")
    protocol_label.pack(pady=5)

    region_label = tk.Label(window, text=f"{get_message('current_region')}: ...", fg="gray")
    region_label.pack(side="bottom", pady=15)

    fetch_subscription(uuid)
    threading.Thread(target=update_checker, daemon=True).start()
    threading.Thread(target=connection_watchdog, args=(uuid,), daemon=True).start()

    if len(sys.argv) > 1 and sys.argv[1] == "1":
        global pending_autostart
        pending_autostart = True
        window.iconify()
    window.mainloop()

if __name__ == "__main__":
    auto_demotion()  
    check_single_instance()
    load_language()  
    check_environment()  
    
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

    create_context_menu(entry_uuid)
    
    saved_uuid = ""
    if UUID_FILE.exists():
        with open(UUID_FILE, "r") as f: saved_uuid = f.read().strip()
        entry_uuid.insert(0, saved_uuid)

    auto_login_enabled = AUTO_LOGIN_FILE.exists()
    
    def do_login():
        u = entry_uuid.get().strip()
        if len(u) > 5:
            
            try:
                response = post_api_with_failover("/login", {"code": u}, timeouts=(6, 8))
                if response.status_code == 200:
                    with open(UUID_FILE, "w") as f: f.write(u)
                    if chk_remember.get():
                        with open(AUTO_LOGIN_FILE, "w") as f: f.write("1")
                    else:
                        if AUTO_LOGIN_FILE.exists(): AUTO_LOGIN_FILE.unlink()
                    login_window.destroy()
                    show_main_window(u)
                else:
                    msg = get_message("invalid_code") if response.status_code in (400, 401, 404) else get_message("expired") if response.status_code == 403 else get_message("server_error")
                    messagebox.showerror("Error", msg)
            except Exception as e:
                messagebox.showerror("Error", f"{get_message('connection_error')}: {e}")
        else:
            messagebox.showerror("Error", get_message("invalid_code"))

    if auto_login_enabled and len(saved_uuid) > 5:
        login_window.destroy()
        show_main_window(saved_uuid)
    else:
        chk_remember = tk.BooleanVar(value=auto_login_enabled)  
        tk.Checkbutton(login_window, text=get_text("auto_login"), variable=chk_remember).pack()
        tk.Button(login_window, text=get_text("login_button"), command=do_login).pack(pady=10)
        
        login_window.mainloop()
