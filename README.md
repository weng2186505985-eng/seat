# HDU 图书馆抢座任务系统 (HDU Seat Snatcher)

这是一个为杭州电子科技大学（HDU）图书馆设计的自动化座位预约系统。支持多账号管理、高精度准时抢座、Token 自动同步以及微信消息推送。

## 🌟 核心功能

- **多账号并行**：支持同时管理多个账号的抢座任务，互不干扰。
- **高精度调度**：自动同步图书馆服务器时间，偏差控制在毫秒级，确保准时冲击。
- **自动凭证同步**：集成 Playwright 模拟登录，自动刷新 api-token，告别手动抓包。
- **智能重试机制**：针对“请求频繁”等错误自动重试，增加成功率。
- **可视化管理**：提供简洁的 Web 界面，随时添加、删除、监控任务状态。
- **微信推送**：通过 Server酱 集成，关键节点（预热、开始、成功/失败）实时推送到手机。

## 🛠️ 环境准备

### 1. 安装 Python 环境
确保你的系统已安装 Python 3.8 或更高版本。

### 2. 安装依赖
在项目根目录下运行：
```bash
pip install -r requirements.txt
```
*(如果没有 `requirements.txt`，请手动安装：`pip install fastapi uvicorn requests playwright pydantic`)*

### 3. 初始化 Playwright
系统依赖 Playwright 进行模拟登录，首次使用需安装浏览器内核：
```bash
playwright install chromium
```

## ⚙️ 配置说明

修改 `config.py` 文件：
- `SCKEY`: 填入你的 [Server酱](https://sct.ftqq.com/) Key，用于微信通知。
- `USERNAME` / `PASSWORD`: 默认登录账号（可选）。
- `COOKIE`: 如果自动登录失效，可手动填入。

## 🚀 快速启动

运行 Web 管理后台：
```bash
python gui_server.py
```
启动后访问 `http://127.0.0.1:8000` 即可进入管理界面。

---

## 📦 使用 PM2 进行进程管理 (推荐)

在生产环境（如服务器）中，推荐使用 `PM2` 来管理 Python 进程，它可以确保程序在崩溃后自动重启，并提供后台运行和日志管理功能。

### 1. 安装 PM2
首先需要安装 Node.js 环境，然后通过 npm 安装 PM2：
```bash
npm install pm2 -g
```

### 2. 启动服务
使用 PM2 启动 `gui_server.py`：
```bash
pm2 start gui_server.py --name hdu-seat --interpreter python
```
*注意：在 Windows 上可能需要指定 python 的完整路径，或者直接使用 `python`（取决于你的环境变量）。*

### 3. 常用 PM2 命令

| 功能 | 命令 |
| --- | --- |
| **查看进程状态** | `pm2 list` |
| **查看实时日志** | `pm2 logs hdu-seat` |
| **重启服务** | `pm2 restart hdu-seat` |
| **停止服务** | `pm2 stop hdu-seat` |
| **删除进程** | `pm2 delete hdu-seat` |
| **设置开机自启** | `pm2 save` (启动后执行) |

## ⚠️ 注意事项
- 请合理使用本工具，遵守图书馆相关规定。
- 建议提前 5 分钟启动程序，系统会自动进行“预热”操作。
- 如果遇到登录失败，请检查账号密码或手动在 `config.py` 更新 Cookie。
