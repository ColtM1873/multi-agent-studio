# Multi-Agent Studio 一键安装脚本（由 setup.bat 调用）

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ---------- 检测管理员 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "本安装需要启用 Windows「长路径支持」，需要管理员权限。`n`n点击「确定」将尝试以管理员身份重新启动。`n若未弹出「用户账户控制」窗口，请关闭本窗口后，右键 setup.bat 选择「以管理员身份运行」。",
        'Multi-Agent Studio 安装', 'OK', 'Information') | Out-Null
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    } catch {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            '自动提权失败，请右键 setup.bat 选择「以管理员身份运行」。',
            'Multi-Agent Studio 安装', 'OK', 'Warning') | Out-Null
    }
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
    exit 1
}
Write-Host '       长路径支持已启用。'

# ---------- 3. 创建虚拟环境 ----------
Write-Host '[3/5] 创建虚拟环境 venv...'
if (-not (Test-Path "$PSScriptRoot\venv")) {
    python -m venv "$PSScriptRoot\venv"
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 创建虚拟环境失败，请截图上方报错信息反馈。'
        exit 1
    }
}

# ---------- 4. 安装依赖 ----------
Write-Host '[4/5] 安装依赖（清华源，首次约需几分钟）...'
& "$PSScriptRoot\venv\Scripts\python.exe" -m pip install -r "$PSScriptRoot\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 依赖安装失败，请截图上方完整报错信息反馈。'
    exit 1
}

# 检测 mcp 版本（langchain-mcp-adapters 0.3.x 要求 mcp<2.0）
$mcpVer = & "$PSScriptRoot\venv\Scripts\python.exe" -c "import importlib.metadata as md; print(md.version('mcp'))" 2>$null
if ($mcpVer -and ([version]$mcpVer -ge [version]'2.0.0')) {
    Write-Host "       检测到不兼容的 mcp $mcpVer，正在重装兼容版本 mcp==1.28.0..."
    & "$PSScriptRoot\venv\Scripts\python.exe" -m pip install "mcp==1.28.0" -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 重装 mcp 失败，请截图上方报错信息反馈。'
        exit 1
    }
}

# ---------- 5. 打包 exe ----------
Write-Host '[5/5] 打包 MultiAgentStudio.exe（首次会自动安装打包工具）...'
& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\build_exe.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 打包 exe 失败，请截图上方完整报错信息反馈。'
    exit 1
}

Write-Host ''
Write-Host '================================================'
Write-Host '  安装完成！已生成 MultiAgentStudio.exe。'
Write-Host '  现在双击它即可启动。'
Write-Host '================================================'
Write-Host ''
