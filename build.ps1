# PrintServer v3 构建脚本（Windows PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $venvPython)) {
    Write-Host "[*] 创建虚拟环境 .venv ..."
    python -m venv (Join-Path $PSScriptRoot ".venv")
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}

Write-Host "[*] 安装依赖 ..."
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt")

Write-Host "[*] 生成图标 ..."
& $venvPython (Join-Path $PSScriptRoot "scripts\make_icon.py")

Write-Host "[*] PyInstaller 打包 ..."
& (Join-Path (Split-Path $venvPython) "pyinstaller.exe") --clean --noconfirm (Join-Path $PSScriptRoot "PrinterService.spec")

Write-Host ""
Write-Host "[+] 完成：$PSScriptRoot\dist\PrintServer.exe"
