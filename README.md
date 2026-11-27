# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。当前阶段通过业务人员本机的 Chrome 浏览器访问运营商系统，并逐步建设页面识别和自动化操作能力。

## 当前设计原则

- VPN 由业务人员手动连接，本程序不自动切换 VPN。
- 浏览器在业务人员本机运行，确保访问流量使用本机 VPN 路由。
- 默认通过 Playwright 控制本机安装的 Google Chrome（`BROWSER_CHANNEL=chrome`），而不是单独下载的 Chromium。
- 浏览器在服务运行期间保持打开，多个任务复用同一页面；同一时间串行执行页面操作。
- 网站连通性检测不等于 VPN 状态检测：程序会根据浏览器导航结果区分网络不可达、网站服务器错误、登录页和预期页面不匹配。
- 本地 API 默认只监听 `127.0.0.1`。

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
python -m playwright install chromium
Copy-Item .env.example .env
```

如果已经创建过 `yunyingxw` 环境，后续只需要执行：

```powershell
conda activate yunyingxw
```

## 启动

确保已激活 `yunyingxw` 环境，并处于项目根目录：

```powershell
python run.py
```

Windows 下通过 `run.py` 设置 Playwright 所需的 Proactor 事件循环，并以单进程模式启动服务。

启动后访问：

- 健康检查：http://127.0.0.1:8000/api/health
- API 文档：http://127.0.0.1:8000/docs

停止服务：在运行服务的终端按 `Ctrl+C`。

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

v0.4.0：增加使用本机安装的 Chrome 作为自动化浏览器的配置。
