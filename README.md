# 运营商报备自动化助手

面向运营商业务报备场景的本机自动化项目。上游 Agent 将任务信息、账号凭据和业务文件一次性传入；本服务负责接收任务、暂存文件，并通过本机 VPN 和专用 Chrome 准备运营商页面。

## 当前设计原则

- VPN 由业务人员按客户要求手动连接，本程序不自动切换 VPN。
- FastAPI 启动时只初始化 Playwright，不主动弹出 Chrome。
- 收到业务任务时，才启动或复用专用 Chrome。
- 专用 Chrome 使用独立用户目录，与日常 Chrome 配置隔离。
- 多个业务任务通过操作锁串行使用同一个浏览器页面。
- 上游密码仅用于本次登录，不写入任务文件、日志或配置。
- 文件按 task_id 独立暂存，单个文件最大 50 MB；本阶段任务文件保存在系统临时目录下的 yunyingxw_tasks 文件夹。
- 当前版本完成任务接收、文件暂存、登录和导航；尚未将文件上传到运营商，也不会点击业务确认按钮。

## 环境要求

- Windows 10/11
- Conda、Python 3.11
- 已安装 Google Chrome
- 业务人员已按公司要求手动连接对应 VPN

## 安装与启动

```powershell
conda activate yunyingxw
python -m pip install -r requirements.txt
python run.py
```

服务地址：http://127.0.0.1:8000  
接口文档：http://127.0.0.1:8000/docs

## 当前保留的接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 检查 FastAPI 服务是否正常运行 |
| GET | `/api/browser/status` | 查看 Chrome/CDP 连接状态，不会启动浏览器 |
| POST | `/api/product-order/batch-import` | 上游 Agent 一次性提交任务信息、账号密码和两个文件；服务暂存文件、登录并进入产品订购申请页面 |
| GET | `/api/page/dom` | 调试接口：读取当前页面和所有 Frame 的可见文本、控件及交互元素 |

旧的独立打开浏览器、单独登录和站点连通性检查接口已合并或移除，业务入口统一为批量订购任务接口。

## 上游 Agent 调用方式

调用 `POST /api/product-order/batch-import`，使用 `multipart/form-data`，当前只需要传入以下五个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| url | 文本 | 运营商系统登录页或入口 URL；服务收到任务后会先打开该地址 |
| username | 文本 | 运营商登录账号 |
| password | 文本 | 运营商登录密码，仅用于本次请求 |
| excel_file | 文件 | 批量导入 Excel，支持 .xls、.xlsx |
| attachment_file | 文件 | 业务附件，支持 PDF、Office、压缩包及常见图片格式 |

任务编号由服务端自动生成，上游无需传入 task_id。

成功响应状态为 `files_received_page_ready`，表示任务字段和两个文件已接收，浏览器已进入“产品订购申请”页面。响应不会返回本机文件绝对路径，也不会回显密码。

### PowerShell 本地联调示例

请使用测试账号和测试文件，不要把真实密码写入脚本或提交到 Git：

```powershell
$uri = "http://127.0.0.1:8000/api/product-order/batch-import"
$form = @{
  url = "http://10.253.61.35/cmopchannel/login"
  username = "测试账号"
  password = "测试密码"
  username = "测试账号"
  password = "测试密码"
  si_code = "201230033"
  product_name = "易速达10694915"
  business_name = "易速达10694915"
  excel_file = Get-Item ".\测试批量导入.xlsx"
  attachment_file = Get-Item ".\测试业务附件.pdf"
}
Invoke-RestMethod -Uri $uri -Method Post -Form $form
```

## 文件暂存位置

文件暂存于系统临时目录下的 `yunyingxw_tasks/<task_id>/`。当前阶段失败时会清理本次任务目录；成功接收后暂时保留，供下一阶段网页上传流程读取。正式接入完整任务生命周期后，需要增加任务完成后的自动清理策略。

## 浏览器配置

专用 Chrome 默认使用独立配置目录 `%LOCALAPPDATA%\ChromeAutomationTest`。可在本地 `.env` 设置：

```ini
BROWSER_CDP_URL=http://127.0.0.1:9222
BROWSER_USER_DATA_DIR=C:/Users/你的Windows用户名/AppData/Local/ChromeAutomationTest
BROWSER_TIMEOUT_MS=20000
```

CDP 仅允许监听本机地址，不要将调试端口暴露到局域网或公网。服务停止时只断开 Playwright 连接，不主动关闭 Chrome，以便保留浏览器会话。
