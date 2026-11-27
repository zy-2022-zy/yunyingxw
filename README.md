# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。当前阶段通过业务人员本机的 Google Chrome 访问运营商系统，并逐步建设页面识别和自动化操作能力。

## 当前设计原则

- VPN 由业务人员手动连接，本程序不自动切换 VPN。
- 浏览器在业务人员本机运行，确保访问流量使用本机 VPN 路由。
- Chrome 由用户通过正常方式启动，并开放仅监听本机的 CDP 调试端口；Playwright 通过 CDP 连接并控制该浏览器，不再自行启动 Chrome。
- 使用独立的自动化 Chrome 用户目录，与日常 Chrome 配置隔离。
- 浏览器在服务运行期间保持打开，多个任务复用同一页面；同一时间串行执行页面操作。
- 网站连通性检测不等于 VPN 状态检测：程序会根据浏览器导航结果区分网络不可达、网站服务器错误、登录页和预期页面不匹配。
- 本地 API 和 CDP 默认只监听本机地址，不要将调试端口暴露到局域网或公网。

## 环境要求

- Windows 10/11
- Conda
- Python 3.11
- 已安装 Google Chrome
- 业务人员已按公司要求安装并手动连接对应 VPN

## 安装

在项目根目录打开 PowerShell：

```powershell
conda create -n yunyingxw python=3.11 -y
conda activate yunyingxw
python -m pip install -r requirements.txt
```

如果已经创建过 `yunyingxw` 环境，后续只需要执行：

```powershell
conda activate yunyingxw
```

## 启动

每次使用时，先启动专用 Chrome，再启动自动化服务。

**1. 启动专用 Chrome**

在 PowerShell 执行：

```powershell
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"

& $chrome `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\ChromeAutomationTest" `
  --no-first-run `
  --no-default-browser-check
```

这个 Chrome 使用独立用户目录，不会占用日常 Chrome 的配置。请保持这个窗口打开。首次使用时可在该窗口中手动完成客户系统登录，后续会复用此浏览器会话。

**2. 确认 CDP 接口可用**

另开一个 PowerShell 窗口执行：

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json/version
```

能返回 Chrome 版本和 `webSocketDebuggerUrl` 即表示接口可用。

**3. 启动自动化服务**

在项目根目录执行：

```powershell
conda activate yunyingxw
python run.py
```

如果本地 `.env` 中需要自定义 CDP 地址，可设置：

```ini
BROWSER_CDP_URL=http://127.0.0.1:9222
```

启动后访问：

- 健康检查：http://127.0.0.1:8000/api/health
- API 文档：http://127.0.0.1:8000/docs

停止服务：在运行服务的终端按 `Ctrl+C`。停止服务只会断开 Playwright 连接，不会主动关闭专用 Chrome。

## 检查运营商网站

在 Swagger 页面打开 `POST /api/site/check`，提交：

```json
{
  "operator": "运营商A",
  "url": "https://运营商提供的客户系统网址"
}
```

请勿提交账号密码、Cookie 或 Token。

## 当前版本

v0.6.0：改为通过 CDP 接管用户正常启动的专用 Chrome。
