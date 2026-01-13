# XiexieVPN Linux Client

XiexieVPN 的官方 Linux 客户端，基于 Python (Tkinter) 和 Xray-core 构建。提供简洁的图形界面，支持 vless+reality 协议，并针对 GNOME 桌面环境进行了系统代理自动配置优化。

## 功能特点

- **图形化界面**: 使用 Tkinter 构建，轻量且无需复杂依赖。
- **自动代理**: 集成 GNOME `gsettings`，自动设置系统 HTTP/SOCKS 代理。
- **自动降权**: 即使以 `sudo` 运行，也会自动降权至当前桌面用户环境，确保图形界面正常显示。
- **智能连接**: 支持订阅链接自动解析、节点自动更新。
- **自愈机制**: 内置 Watchdog，连接异常时自动刷新配置。

## 系统要求

- **操作系统**: Ubuntu 20.04/22.04/24.04, Debian 10+ (推荐 GNOME 桌面环境)
- **Python**: Python 3.8+
- **依赖库**: `python3-tk`, `python3-venv`

## 快速构建

项目包含一键构建脚本 `build_linux.sh`，可自动处理依赖并打包为单文件可执行程序。

### 1. 克隆代码

```bash
git clone <your-repo-url>
cd linux
```

### 2. 运行构建脚本

脚本会自动安装 `apt` 依赖、创建虚拟环境、下载 Xray 核心并进行 PyInstaller 打包。

```bash
chmod +x build_linux.sh
./build_linux.sh
```

构建完成后，可执行文件位于：
`build_work/dist/xiexievpn_linux`

## 手动开发运行

如果你想在开发模式下运行（无需打包）：

1. **安装依赖**:
   ```bash
   sudo apt install python3-tk python3-pil.imagetk
   pip install -r requirements.txt  # (如果没有 requirements.txt，参考 build_linux.sh 中的 pip install)
   pip install requests pillow
   ```

2. **准备资源**:
   确保目录下有 `languages.json`, `favicon.ico` 以及 `xray` 二进制文件（需放在代码引用的路径下，或参考脚本逻辑）。

3. **运行**:
   ```bash
   python3 main_linux.py
   ```

## 文件结构

- `main_linux.py`: 客户端主逻辑（UI、网络、代理控制）。
- `build_linux.sh`: 自动化构建脚本。
- `languages.json`: 多语言支持文件。
- `run_as_zfz.sh`: 开发调试用的辅助脚本。

## 注意事项

- **系统代理**: 目前主要针对 GNOME 桌面环境优化 (`gsettings`)。KDE 或其他桌面环境可能需要手动设置代理 (HTTP: 1080, SOCKS: 10809)。
- **权限**: 脚本包含自动降权逻辑，建议直接作为普通用户运行。
