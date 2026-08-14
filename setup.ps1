# Multi-Agent Studio 一键安装脚本（由 setup.bat 调用）

$ErrorActionPreference = 'Stop'

# ---------- 检测管理员 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "本安装需要启用 Windows「长路径支持」，否则安装 torch 时会报「路径过长」错误。`n`n即将弹出「用户账户控制」窗口，请点击「是」。",
        'Multi-Agent Studio 安装', 'OK', 'Information') | Out-Null
    Start-Process -FilePath $PSCommandPath -Verb RunAs
    exit
}

Write-Host ''
Write-Host '================================================'
Write-Host '  Multi-Agent Studio 一键安装'
Write-Host '================================================'
Write-Host ''

# ---------- 1. 检查 Python ----------
Write-Host '[1/5] 检查 Python...'
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host ''
    Write-Host '[错误] 未检测到 Python！'
    Write-Host '请先到 https://www.python.org/downloads/ 安装 Python 3.13，'
    Write-Host '安装时请勾选 "Add python.exe to PATH"，然后重新运行本脚本。'
    Read-Host '按回车退出'
    exit 1
}
python --version

# ---------- 2. 启用长路径（硬性前置） ----------
Write-Host '[2/5] 启用 Windows 长路径支持...'
try {
    Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1 -Type DWord
    $v = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled).LongPathsEnabled
    if ($v -ne 1) { throw '验证失败' }
} catch {
    Write-Host ''
    Write-Host '[错误] 无法启用 Windows 长路径支持（注册表写入失败）。'
    Write-Host '请确认已以管理员身份运行本脚本后重试。'
    Read-Host '按回车退出'
    exit 1
}
Write-Host '       长路径支持已启用。'

# ---------- 3. 创建虚拟环境 ----------
Write-Host '[3/5] 创建虚拟环境 venv...'
if (-not (Test-Path 'venv')) {
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 创建虚拟环境失败，请截图上方报错信息反馈。'
        Read-Host '按回车退出'
        exit 1
    }
}

# ---------- 4. 安装依赖 ----------
Write-Host '[4/5] 安装依赖（清华源，首次约需几分钟）...'
& 'venv\Scripts\python.exe' -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 依赖安装失败，请截图上方完整报错信息反馈。'
    Read-Host '按回车退出'
    exit 1
}

# ---------- 5. 打包 exe ----------
Write-Host '[5/5] 打包 MultiAgentStudio.exe（首次会自动安装打包工具）...'
& 'venv\Scripts\python.exe' build_exe.py
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 打包 exe 失败，请截图上方完整报错信息反馈。'
    Read-Host '按回车退出'
    exit 1
}

Write-Host ''
Write-Host '================================================'
Write-Host '  安装完成！已生成 MultiAgentStudio.exe。'
Write-Host '  现在双击它即可启动。'
Write-Host '================================================'
Write-Host ''
Read-Host '按回车退出'
