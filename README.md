# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。业务人员只需按要求连接 VPN，自动化服务负责启动专用 Chrome、接管浏览器并执行网页操作。

## 当前设计原则

- VPN 由业务人员手动连接，本程序不自动切换 VPN。
- 自动化服务启动时自动启动专用 Chrome，并通过 CDP 接管。
- 专用 Chrome 使用独立用户目录，与业务人员日常 Chrome 配置隔离。
- 浏览器在服务运行期间保持打开；服务停止时，只关闭由本服务启动的 Chrome。
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

## 启动

以后正常使用时，只需要在项目根目录执行：

```powershell
conda activate yunyingxw
python run.py
```

服务会自动查找并启动 Chrome，使用独立配置目录 `%LOCALAPPDATA%\ChromeAutomationTest`，然后通过 CDP 连接。首次使用时，专用 Chrome 会显示出来；如客户系统需要登录或验证码，业务人员可在该专用窗口中完成必要的人工验证。登录状态会保存在专用配置目录中。

如果需要自定义配置，可在本地 `.env` 中设置：

```ini
BROWSER_CDP_URL=http://127.0.0.1:9222
BROWSER_USER_DATA_DIR=C:/Users/你的Windows用户名/AppData/Local/ChromeAutomationTest
BROWSER_TIMEOUT_MS=20000
```

启动后访问：

- 健康检查：http://127.0.0.1:8000/api/health
- API 文档：http://127.0.0.1:8000/docs

停止服务：在运行服务的终端按 `Ctrl+C`。本服务启动的 Chrome 会随服务停止而关闭；如果服务连接的是事先手动启动的 CDP Chrome，则不会关闭该浏览器。

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

v0.7.0：自动启动专用 Chrome，并通过 CDP 接管；Git 提交说明统一使用中文。
