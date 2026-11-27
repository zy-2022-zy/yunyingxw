# 一键打包脚本：生成免安装部署包（内嵌 Python 运行时 + 依赖 + 业务代码）。
# 用法：powershell -ExecutionPolicy Bypass -File build_package.ps1
# 产物：dist/运营商报备助手/ 目录和 dist/运营商报备助手.zip
# 业务人员电脑要求：已安装 Google Chrome、已按公司要求连接 VPN；无需安装 Python。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pyVersion = "3.11.9"
$embedUrl = "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip"
$distDir = Join-Path $root "dist"
$pkgName = "运营商报备助手"
$pkgDir = Join-Path $distDir $pkgName
$downloadDir = Join-Path $distDir "_downloads"
$embedZip = Join-Path $downloadDir "python-$pyVersion-embed-amd64.zip"

Write-Host "== 1/6 准备输出目录 =="
if (Test-Path $pkgDir) { Remove-Item -Recurse -Force $pkgDir }
New-Item -ItemType Directory -Force -Path $pkgDir, $downloadDir | Out-Null

Write-Host "== 2/6 下载内嵌 Python $pyVersion =="
if (-not (Test-Path $embedZip)) {
    Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
}

Write-Host "== 3/6 解压 Python 运行时 =="
$runtimeDir = Join-Path $pkgDir "runtime"
Expand-Archive -Path $embedZip -DestinationPath $runtimeDir -Force

# 内嵌 Python 默认只认 python311.zip 和当前目录；放开路径限制并加入依赖目录和上级业务代码目录。
$pthFile = Join-Path $runtimeDir "python311._pth"
@"
python311.zip
.
..
Lib\site-packages
import site
"@ | Set-Content -Path $pthFile -Encoding ascii

Write-Host "== 4/6 安装依赖到包内（使用 yunyingxw 环境的 pip 解析依赖）=="
$siteDir = Join-Path $runtimeDir "Lib\site-packages"
conda run -n yunyingxw python -m pip install --target $siteDir -r (Join-Path $root "requirements.txt") --quiet --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

Write-Host "== 5/6 复制业务代码 =="
Copy-Item -Recurse -Force (Join-Path $root "app") (Join-Path $pkgDir "app")
Copy-Item -Force (Join-Path $root "run.py") $pkgDir
Copy-Item -Force (Join-Path $root "README.md") $pkgDir
Copy-Item -Force (Join-Path $root ".env.example") (Join-Path $pkgDir ".env")

# 清理 __pycache__，减小包体积。
Get-ChildItem -Path $pkgDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 启动脚本：双击即可启动；报错时窗口不会一闪而过。
$batPath = Join-Path $pkgDir "启动.bat"
$batContent = @"
@echo off
chcp 65001 >nul
title 运营商报备自动化助手
cd /d "%~dp0"

if not exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" if not exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    echo [警告] 未检测到 Google Chrome，请先安装 Chrome 再使用本工具。
)

echo 请确认已按公司要求连接 VPN，然后保持本窗口开启。
echo 启动后接口文档地址：http://127.0.0.1:8000/docs
echo 关闭本窗口即停止服务。
echo.
runtime\python.exe run.py

echo.
echo 服务已退出。如上方有报错信息，请截图反馈。
pause
"@
# cmd.exe 不识别带 BOM 的 bat，且需要 CRLF 换行。
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($batPath, ($batContent -replace "`n", "`r`n"), $utf8NoBom)

Write-Host "== 6/6 压缩为 zip =="
$zipPath = Join-Path $distDir "$pkgName.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path $pkgDir -DestinationPath $zipPath -Force

$sizeMB = [math]::Round((Get-ChildItem $pkgDir -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "打包完成：$pkgDir（约 $sizeMB MB）"
Write-Host "压缩包：$zipPath"
