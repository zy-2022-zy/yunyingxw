# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。业务人员只需按要求连接 VPN；业务请求通过 FastAPI 发起，服务按需启动并管理专用 Chrome。

## 当前设计原则

- VPN 由业务人员手动连接，本程序不自动切换 VPN。
- FastAPI 启动时只初始化自动化服务，不主动弹出 Chrome。
- 收到打开网页或业务操作请求时，才启动专用 Chrome 并通过 CDP 接管。
- 专用 Chrome 使用独立用户目录，与业务人员日常 Chrome 配置隔离。
- 浏览器被关闭或 CDP 连接断开后，后续请求会尝试重新连接；无法连接时自动重新启动专用 Chrome。
- 服务停止时只断开 Playwright，不主动关闭 Chrome；专用 Chrome 保持运行，便于下次启动服务时复用登录会话。
- 已经存在可连接的 CDP 浏览器时，服务会优先连接，不会主动关闭该外部浏览器。
- CDP 仅监听本机地址，不要将调试端口暴露到局域网或公网。
- 多个任务复用同一页面，并通过操作锁串行执行。

## 环境要求

- Windows 10/11
- Conda、Python 3.11
- 已安装 Google Chrome
- 业务人员已按公司要求手动连接对应 VPN

## 安装

在项目根目录打开 PowerShell：

```powershell
conda activate yunyingxw
python -m pip install -r requirements.txt
```

## 启动服务

在项目根目录执行：

```powershell
conda activate yunyingxw
python run.py
```

此时只启动 FastAPI，不会自动弹出 Chrome。

服务地址：http://127.0.0.1:8000
API 文档：http://127.0.0.1:8000/docs

## 通过 FastAPI 打开网页

调用 `POST /api/browser/open`：

```json
{
  "url": "http://10.253.61.35/cmopchannel/login"
}
```

服务收到请求后会检查 CDP 浏览器连接：
- 已有可用浏览器：直接复用。
- 没有可用浏览器：自动启动专用 Chrome。
- 浏览器被关闭：后续打开网页请求会尝试重新启动浏览器。

成功响应示例：

```json
{
  "status": "opened",
  "message": "网页已在专用 Chrome 中打开。",
  "final_url": "http://10.253.61.35/cmopchannel/login",
  "page_title": "合作伙伴管理系统",
  "http_status": 200
}
```

## 浏览器状态

调用 `GET /api/browser/status` 查看当前连接状态。该接口只检查状态，不会为了检查而启动 Chrome。

其他现有接口：
- `POST /api/login`：使用本次请求提交的账号密码执行登录；凭据仅用于当前请求，不写入配置或文件。
- `GET /api/health`：服务健康检查
- `POST /api/site/check`：检查运营商网站可达性
- `GET /api/page/dom`：读取当前页面 DOM 摘要

## 浏览器配置

专用 Chrome 默认使用独立配置目录 `%LOCALAPPDATA%\\ChromeAutomationTest`。如需自定义，可在本地 `.env` 设置：

```ini
BROWSER_CDP_URL=http://127.0.0.1:9222
BROWSER_USER_DATA_DIR=C:/Users/你的Windows用户名/AppData/Local/ChromeAutomationTest
BROWSER_TIMEOUT_MS=20000
```

停止服务：在运行服务的终端按 `Ctrl+C`。服务会断开 Playwright 连接，但保留 Chrome 进程运行；下次启动服务后会优先连接原来的 Chrome 和配置目录。若需要完全退出浏览器，请手动关闭专用 Chrome 窗口。

登录接口 `POST /api/login` 需要在本机调用，账号密码通过请求体传入，仅用于当前登录操作；不要将请求转发到公网或局域网，也不要提交 Cookie 或 Token。

## 自动登录接口

调用 `POST /api/login`，请求体示例：

```json
{
  "username": "你的登录账号",
  "password": "你的登录密码"
}
```

接口会在专用 Chrome 中完成登录；登录成功后自动点击“我的产品”，等待产品页面加载，再点击“产品订购申请”。如果页面出现多个同名入口，接口会停止自动点击并返回 `navigation_failed`，避免误选。响应状态包括 `login_success`、`verification_required`、`login_failed`、`navigation_failed`。账号密码不会写入项目文件或配置；请仅通过本机 `127.0.0.1` 调用，不要把请求体或凭据分享给他人。

## 当前版本

v0.9.0：增加运营商系统自动登录接口，支持请求时传入账号密码并返回登录状态。
