# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。第一阶段先完成：在业务人员手动连接 VPN 后，通过本机 Playwright 浏览器验证运营商网站是否可访问。

## 当前设计原则

- VPN 由业务人员手动连接，本程序不自动切换 VPN。
- 浏览器在业务人员本机运行，确保访问流量使用本机 VPN 路由。
- 网站连通性检测不等于 VPN 状态检测：程序会根据浏览器导航结果区分网络不可达、网站服务器错误、登录页和预期页面不匹配。
- 后续运营商页面操作将封装为独立适配器，不把不同运营商的 DOM 选择器混在一起。
- 本地 API 默认只监听 `127.0.0.1`。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Chrome/Chromium 所需运行依赖
- 业务人员已按公司要求安装并手动连接对应 VPN

## 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

## 启动

```powershell
python -m app.main
```

启动后访问：

- 健康检查：http://127.0.0.1:8000/api/health
- API 文档：http://127.0.0.1:8000/docs

## 检查运营商网站

在 Swagger 页面打开 `POST /api/site/check`，提交：

```json
{
  "operator": "运营商A",
  "url": "https://运营商提供的客户系统网址",
  "expected_text": null
}
```

当前阶段需要你提供真实客户系统网址，才能验证实际 VPN 网络下的访问结果。请勿提交账号密码、Cookie 或 Token。

## 当前版本

v0.1.0：项目骨架、本机 Playwright 浏览器管理、网站可访问性检测 API。
