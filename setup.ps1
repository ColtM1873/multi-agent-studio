# Multi-Agent Studio 一键安装脚本（由 setup.bat 调用）
#
# 权限策略：默认就用当前用户权限安装，不再一上来就要求管理员。
# 只有遇到确实需要管理员才能完成的步骤时才提示提权并重跑本脚本：
#   * 依赖安装 / 打包等步骤因权限或长路径限制失败；
#   * 需要安装 pgvector（写入 PostgreSQL 安装目录）。
# 提权重跑会带上 -Mode / -OriginalUser / -OriginalSid，跳过版本菜单；
# 退出提权后再由原登录用户的进程创建桌面快捷方式。

param(
    [string]$Mode = '',
    [string]$OriginalUser = '',
    [string]$OriginalSid = '',
    [switch]$InstallPgvector
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ---------- 桌面快捷方式辅助 ----------
# 提权前会记录原登录用户（见下方「记录提权前的登录用户」），提权后据此决定：
#   同一账户 → 直接创建（管理员账户的桌面就是原用户桌面）；
#   不同账户 → 提权后的子进程只留交接标记，退出提权后由原用户父进程按真正的用户桌面创建。
$pendingShortcutMarker = Join-Path $PSScriptRoot '.setup_shortcut_pending'
if (Test-Path -LiteralPath $pendingShortcutMarker) {
    Remove-Item -LiteralPath $pendingShortcutMarker -Force -ErrorAction SilentlyContinue
}

function New-StudioDesktopShortcut {
    param([string]$ExeName)
    $exePath = Join-Path $PSScriptRoot $ExeName
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "未找到 $ExeName（打包可能未完成）"
    }
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ([string]::IsNullOrWhiteSpace($desktop)) {
        throw '当前用户没有可用的桌面目录'
    }
    $lnkPath = Join-Path $desktop 'MultiAgent Studio.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = $exePath
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = "$exePath,0"
    $shortcut.Description = 'Multi-Agent Studio'
    $shortcut.Save()
    return $lnkPath
}

function Start-ElevatedSetupChild {
    # 以管理员身份重新运行本脚本并等待结束；返回子进程退出码（取不到时返回 $null）。
    # 返回 2 表示「没能启动提权进程」（例如用户在 UAC 点了否）——调用方据此区分
    # 「用户拒绝提权」和「管理员安装真的失败了」。
    # 子进程负责全部安装步骤；退出提权后由本进程（原登录用户）补建桌面快捷方式。
    $elevArgList = @(
        '-NoProfile'
        '-ExecutionPolicy Bypass'
        '-File'
        "`"$PSCommandPath`""
        '-Mode'
        $Mode
        '-OriginalUser'
        "`"$origUserName`""
        '-OriginalSid'
        $origUserSid
    )
    if ($InstallPgvector) {
        $elevArgList += '-InstallPgvector'
    }
    $elevArgList = $elevArgList -join ' '

    try {
        $elevatedProc = Start-Process -FilePath 'powershell.exe' -ArgumentList $elevArgList -Verb RunAs -PassThru
    } catch {
        Write-Host ''
        Write-Host '       [提示] 未能获得管理员权限（可能点了「否」）。'
        Write-Host '             可稍后右键 setup.bat 选择「以管理员身份运行」。'
        return 2
    }
    if (-not $elevatedProc) {
        Write-Host '[错误] 未能获取管理员安装进程，请重试。'
        return 2
    }

    Write-Host ''
    Write-Host '已请求管理员权限，安装将在新打开的管理员窗口中继续。'
    Write-Host '本窗口会等待安装结束；若桌面快捷方式尚未创建，结束后会回到当前登录用户的桌面补建。'

    # 等待提权后的安装进程结束（= 退出提权），之后本进程仍在原用户上下文中
    $childExited = $false
    $childExitCode = $null
    try {
        $elevatedProc.WaitForExit()
        $childExited = $true
        try { $childExitCode = $elevatedProc.ExitCode } catch { }
    } catch {
        # 少数环境下非提权进程无法等待提权进程，退化为轮询进程是否存在
    }
    if (-not $childExited) {
        while (Get-Process -Id $elevatedProc.Id -ErrorAction SilentlyContinue) {
            Start-Sleep -Milliseconds 500
        }
    }

    if ($null -ne $childExitCode -and $childExitCode -ne 0) {
        Write-Host ''
        Write-Host "       [警告] 管理员安装进程未成功结束（退出码 $childExitCode），安装可能未完成。"
        Write-Host '             请查看管理员安装窗口中的报错信息后重试。'
        return 1
    }

    if (Test-Path -LiteralPath $pendingShortcutMarker) {
        # 提权账户与原登录用户不同：现在已退出提权，由原用户进程在真正的桌面创建
        try {
            $lnkPath = New-StudioDesktopShortcut -ExeName $exeName
            Remove-Item -LiteralPath $pendingShortcutMarker -Force -ErrorAction SilentlyContinue
            Write-Host ''
            Write-Host '================================================'
            Write-Host '  安装完成！'
            Write-Host "  已在当前登录用户的桌面创建快捷方式：$lnkPath"
            Write-Host '================================================'
            return 0
        } catch {
            Write-Host ''
            Write-Host "       [警告] 创建桌面快捷方式失败：$($_.Exception.Message)"
            Write-Host "             可手动右键 $exeName → 发送到 → 桌面快捷方式，并重命名为「MultiAgent Studio」。"
            return 1
        }
    }

    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot $exeName)) {
        # 同一账户提权：管理员安装进程通常已直接创建好；若没建成（例如 COM 失败），这里补建
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnkPath = if ($desktop) { Join-Path $desktop 'MultiAgent Studio.lnk' } else { '' }
        if (-not $lnkPath -or -not (Test-Path -LiteralPath $lnkPath)) {
            try {
                $lnkPath = New-StudioDesktopShortcut -ExeName $exeName
            } catch {
                Write-Host ''
                Write-Host "       [警告] 创建桌面快捷方式失败：$($_.Exception.Message)"
                Write-Host "             可手动右键 $exeName → 发送到 → 桌面快捷方式，并重命名为「MultiAgent Studio」。"
                return 1
            }
        }
        Write-Host ''
        Write-Host '================================================'
        Write-Host '  安装完成！'
        Write-Host "  桌面快捷方式已就绪：$lnkPath"
        Write-Host '================================================'
        return 0
    }

    Write-Host ''
    Write-Host "       [警告] 未找到 $exeName，安装可能未完成。"
    Write-Host '             请查看管理员安装窗口中的报错信息后重试。'
    return 1
}

function Request-ElevatedInstall {
    # 普通权限下安装跑不通时调用：说明原因，询问是否提权重跑。
    # 返回子进程退出码；用户选择不提权时返回 $null。
    param([string]$Reason)
    Write-Host ''
    Write-Host '------------------------------------------------'
    Write-Host "  安装没有继续下去：$Reason"
    Write-Host '  这类问题常见于没有管理员权限（Windows 长路径限制、写入系统设置等）。'
    Write-Host '  选择 Y 后，安装会以管理员身份重新执行；已下载/已安装的内容会直接复用。'
    Write-Host '------------------------------------------------'
    $ans = Read-Host '  输入 Y 以管理员身份重新运行安装；直接回车退出 [Y/N]'
    if ($ans -notmatch '^(y|yes)$') {
        return $null
    }
    return Start-ElevatedSetupChild
}

function Stop-InstallStep {
    # 安装步骤失败的统一出口：普通权限下先给一次提权重跑的机会。
    param([string]$Message, [string]$Reason)
    Write-Host ''
    Write-Host $Message
    if (-not $isAdmin) {
        $code = Request-ElevatedInstall -Reason $Reason
        if ($null -ne $code) {
            if ($code -eq 2) { exit 1 }   # 用户拒绝提权 / UAC 失败
            exit $code
        }
    }
    exit 1
}

$interactive = [string]::IsNullOrWhiteSpace($Mode)
if ($interactive) {
    Write-Host ''
    Write-Host '================================================='
    Write-Host '  Multi-Agent Studio 安装版本选择'
    Write-Host '================================================='
    Write-Host ''
    Write-Host '  [1] 普通版（推荐，功能完整）'
    Write-Host '      约需 9 GB 存储空间：包含本地 embedding 模型与运行库，'
    Write-Host '      支持长期记忆、记忆读写、记忆吸附与记忆库管理。'
    Write-Host ''
    Write-Host '  [2] Lite 轻量版'
    Write-Host '      占用更少：无需下载 embedding 模型，运行库也更小。'
    Write-Host '      不含长期记忆、记忆读写、记忆吸附与记忆库管理；'
    Write-Host '      其余功能与普通版一致。'
    Write-Host ''
    Write-Host '  请按数字键 1 或 2 选择版本（只接受 1 或 2），选好后按回车确认。'
    do {
        $modeAns = Read-Host '  1=普通版，2=Lite 轻量版'
    } until ($modeAns -match '^\s*[12]\s*$')
    if ($modeAns -match '^\s*2\s*$') { $Mode = 'Lite' } else { $Mode = 'Full' }
}

if ($Mode -notin @('Full', 'Lite')) {
    Write-Host "[错误] 未知安装版本：$Mode（可选 Full / Lite）"
    exit 1
}

$modeLabel = if ($Mode -eq 'Lite') { 'Lite 轻量版' } else { '普通版' }
$exeName = if ($Mode -eq 'Lite') { 'MultiAgentStudio-Lite.exe' } else { 'MultiAgentStudio.exe' }

if ($interactive) {
    Write-Host ''
    Write-Host "  已选择：$modeLabel"
    do {
        $confirmAns = Read-Host '  请按回车键开始安装（直接关闭窗口可取消）'
        if ($confirmAns) {
            Write-Host ''
            Write-Host '  [提示] 请直接按回车键继续，不要输入其它内容。'
        }
    } while ($confirmAns)
}

# ---------- 记录提权前的登录用户 ----------
# 提权后 Windows 的「桌面」会解析成管理员账户的桌面：先记下当前登录用户的 SID，
# 提权后的子进程拿它和自身 SID 比对，决定快捷方式该直接建还是退回原用户进程再建。
$origIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$origUserName = $origIdentity.Name
$origUserSid = $origIdentity.User.Value
$isAdmin = ([Security.Principal.WindowsPrincipal]$origIdentity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host ''
Write-Host '================================================'
Write-Host "  Multi-Agent Studio 一键安装（$modeLabel）"
Write-Host '================================================'
Write-Host ''
if ($isAdmin) {
    Write-Host '  当前已是管理员权限，将直接完成全部安装步骤。'
} else {
    Write-Host '  将以当前用户权限安装；只有遇到确实需要管理员的步骤时，才会提示提权重跑。'
}
Write-Host ''

# ---------- 1. 检查 Python ----------
Write-Host '[1/8] 检查 Python...'
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host ''
    Write-Host '[错误] 未检测到 Python！'
    Write-Host '请先到 https://www.python.org/downloads/ 安装 Python 3.13，'
    Write-Host '安装时请勾选 "Add python.exe to PATH"，然后重新运行本脚本。'
    exit 1
}
python --version

# ---------- 2. 启用长路径（优先，但不再强制提权）----------
# 长路径对 torch 这类依赖很关键，但普通用户改不了 HKLM。这里先只读检测：
# 已启用就什么都不做；未启用且当前不是管理员就先继续安装，等真的因路径过长
# 失败时再走「提权重跑」；已是管理员则照旧直接写入并校验。
Write-Host '[2/8] 检查 Windows 长路径支持...'
$longPathKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
$longPathsEnabled = $false
try {
    $lpValue = (Get-ItemProperty -Path $longPathKey -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
    $longPathsEnabled = ($lpValue -eq 1)
} catch {
}

if ($longPathsEnabled) {
    Write-Host '       长路径支持已启用，无需修改。'
} elseif ($isAdmin) {
    try {
        Set-ItemProperty -Path $longPathKey -Name LongPathsEnabled -Value 1 -Type DWord
        $v = (Get-ItemProperty -Path $longPathKey -Name LongPathsEnabled).LongPathsEnabled
        if ($v -ne 1) { throw '验证失败' }
        $longPathsEnabled = $true
        Write-Host '       长路径支持已启用。'
    } catch {
        Write-Host ''
        Write-Host '[错误] 无法启用 Windows 长路径支持（注册表写入失败）。'
        Write-Host '请确认已以管理员身份运行本脚本后重试。'
        exit 1
    }
} else {
    Write-Host '       [提示] 当前未启用长路径支持；修改系统设置需要管理员权限。'
    Write-Host '             先继续以普通权限安装：如果依赖安装因「路径过长」失败，'
    Write-Host '             安装流程会再提示你以管理员身份重试（届时会自动启用）。'
}

# ---------- 2.5 修复「低完整性」标签（环境自愈）----------
# 某些下载器/网盘客户端/沙箱解压工具会把项目文件夹打成 Windows「低完整性(Low)」，
# 并被整棵目录树继承；此后 venv 与 exe 都以低完整性运行，无法写入 %TEMP%、%LOCALAPPDATA%
# 等中完整性位置，导致 PyInstaller 打包失败、MultiAgentStudio.exe 启动报
# "Could not create temporary directory!"。此处统一重置为「中完整性(Medium)」。
try {
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    if ((& $icacls $PSScriptRoot 2>$null | Out-String) -match 'Mandatory Label\\Low') {
        Write-Host '       检测到项目文件夹为「低完整性」，正在修复...'
        & $icacls $PSScriptRoot /setintegritylevel (OI)(CI)M /T /C | Out-Null
        Write-Host '       已重置为「中完整性」。'
    }
} catch {
    Write-Host "       [警告] 完整性标签检查/修复失败：$($_.Exception.Message)"
}

# ---------- 3. 创建虚拟环境 ----------
Write-Host '[3/8] 创建虚拟环境 venv...'
if (-not (Test-Path "$PSScriptRoot\venv")) {
    python -m venv "$PSScriptRoot\venv"
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallStep -Message '[错误] 创建虚拟环境失败，请截图上方报错信息反馈。' -Reason '创建虚拟环境失败（可能是权限或长路径限制）'
    }
}

# ---------- 4. 安装依赖 ----------
$reqFile = Join-Path $PSScriptRoot 'requirements.txt'
if ($Mode -eq 'Lite') {
    $reqFile = Join-Path $PSScriptRoot 'build\requirements-lite.txt'
    & "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\lite\make_requirements.py" $reqFile
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallStep -Message '[错误] 生成 Lite 依赖清单失败，请截图上方报错信息反馈。' -Reason '生成 Lite 依赖清单失败'
    }
}
Write-Host "[4/8] 安装 $modeLabel 依赖（清华源，首次约需几分钟）..."
& "$PSScriptRoot\venv\Scripts\python.exe" -m pip install -r $reqFile -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    $longPathHint = ''
    if (-not $longPathsEnabled) {
        $longPathHint = '（若报错涉及「路径过长 / filename too long / WinError 206」，通常是长路径支持未启用。）'
    }
    Stop-InstallStep -Message "[错误] 依赖安装失败，请截图上方完整报错信息反馈。$longPathHint" -Reason '依赖安装失败（可能是长路径或权限限制）'
}

# 检测 mcp 版本（langchain-mcp-adapters 0.3.x 要求 mcp<2.0）
$mcpVer = & "$PSScriptRoot\venv\Scripts\python.exe" -c "import importlib.metadata as md; print(md.version('mcp'))" 2>$null
if ($mcpVer -and ([version]$mcpVer -ge [version]'2.0.0')) {
    Write-Host "       检测到不兼容的 mcp $mcpVer，正在重装兼容版本 mcp==1.28.0..."
    & "$PSScriptRoot\venv\Scripts\python.exe" -m pip install "mcp==1.28.0" -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallStep -Message '[错误] 重装 mcp 失败，请截图上方报错信息反馈。' -Reason '重装 mcp 失败'
    }
}

# 校验浏览器接管运行时依赖（websocket-client / requests）；缺失会导致「打开浏览器」报 ModuleNotFoundError
$missingDeps = & "$PSScriptRoot\venv\Scripts\python.exe" -c "import importlib.util as u; print(','.join(m for m in ['websocket','requests'] if u.find_spec(m) is None))" 2>$null
if ($missingDeps) {
    Write-Host "       检测到缺失依赖 [$missingDeps]，正在补装..."
    & "$PSScriptRoot\venv\Scripts\python.exe" -m pip install -r $reqFile -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Stop-InstallStep -Message '[错误] 补装依赖失败，请截图上方报错信息反馈。' -Reason '补装依赖失败'
    }
}

# 校验桌面应用窗口运行时依赖（pywebview / pythonnet）；缺失会回退到默认浏览器
$missingWebview = & "$PSScriptRoot\venv\Scripts\python.exe" -c "import importlib.util as u; print(','.join(m for m in ['webview','pythonnet'] if u.find_spec(m) is None))" 2>$null
if ($missingWebview) {
    Write-Host "       检测到缺失桌面窗口依赖 [$missingWebview]，正在补装..."
    & "$PSScriptRoot\venv\Scripts\python.exe" -m pip install -r $reqFile -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host '       [警告] 桌面窗口依赖安装失败，程序将回退到默认浏览器。'
    }
}

# 检测 Microsoft Edge WebView2 运行时（独立窗口的渲染内核）；缺失则回退默认浏览器
$wv2 = @'
import winreg, sys
p = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
for h in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
    try:
        winreg.OpenKey(h, p)
        print("1")
        sys.exit()
    except OSError:
        pass
print("0")
'@ | & "$PSScriptRoot\venv\Scripts\python.exe" -
if ($wv2 -ne '1') {
    Write-Host '       [提示] 未检测到 Microsoft Edge WebView2 运行时，界面将回退到默认浏览器。'
    Write-Host '             如需独立应用窗口，请安装 WebView2 Runtime：'
    Write-Host '             https://developer.microsoft.com/microsoft-edge/webview2/'
}

# 系统级「停止/继续」悬浮按钮依赖标准库 tkinter（无法用 pip 安装）；缺失时仅警告
& "$PSScriptRoot\venv\Scripts\python.exe" -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '       [警告] 未检测到 tkinter，浏览器接管的系统级「停止/继续」悬浮按钮将不可用。'
    Write-Host '             可在重装 Python 时勾选 "tcl/tk and IDLE" 修复该组件。'
}

# ---------- 4.5 检测旧版 store 数据库（只记录迁移源，不迁移、不删库）----------
# 旧版本每个 multi-agent 可以填不同的 store 数据库。新版本统一使用全局 store 数据库
# （默认 Long_Term_Memory）。这里把配置里的旧库名落盘成迁移源，程序启动后会弹窗询问
# 是否迁移；也可以在「系统设置 → 全局配置设置」里随时迁移。脚本只读配置、不碰数据库。
Write-Host ''
Write-Host '[4.5/8] 检查旧版 store 数据库...'
& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\scripts\detect_store_migrations.py"
if ($LASTEXITCODE -eq 0) {
    Write-Host '       检查完成。若发现旧版 store 数据库，启动程序后可在「系统设置 → 全局配置设置」中迁移。'
} else {
    Write-Host '       [提示] 旧版 store 数据库检查未完成；程序启动后也会自动检测。'
}

# ---------- 5. 可选：一键安装 pgvector（已安装 / 无 PostgreSQL 则跳过询问）----------
# pgvector 必须写入 PostgreSQL 安装目录，是真正需要管理员的步骤。
# 普通权限下先不擅自启动安装：明确询问后提权重跑；管理员则就地安装。
$pgvSkipped = $false
Write-Host ''
if ($Mode -eq 'Lite') {
    Write-Host '[5/8] Lite 版不使用长期记忆 store，跳过 pgvector 检查。'
} else {
    Write-Host '[5/8] 检查 PostgreSQL 的 pgvector 扩展（程序的长期记忆需要它）...'
    $pgvState = 'unknown'   # installed | missing | nopg | unknown
    try {
        & "$PSScriptRoot\install_pgvector.ps1" -Status
        switch ($LASTEXITCODE) {
            0 { $pgvState = 'installed' }
            1 { $pgvState = 'missing' }
            2 { $pgvState = 'nopg' }
            default { $pgvState = 'unknown' }
        }
    } catch {
        $pgvState = 'unknown'
    }

    if ($pgvState -eq 'installed') {
        Write-Host '       已检测到 pgvector，无需重复安装，跳过。'
    } elseif ($pgvState -eq 'nopg') {
        Write-Host '       未检测到 PostgreSQL 安装，跳过（安装 PostgreSQL 后可双击 install_pgvector.bat 单独安装）。'
    } else {
        if ($pgvState -eq 'unknown') {
            Write-Host '       未能自动确认 pgvector 状态；如已安装可直接回车跳过。'
        }
        Write-Host '       说明：会自动识别 PostgreSQL 安装目录与 Visual Studio 编译环境；'
        Write-Host '             没有编译环境时会询问是否使用社区预编译包（会有风险提示）。'
        if ($isAdmin) {
            $pgvAns = if ($InstallPgvector) { 'y' } else { Read-Host '       输入 Y 立即安装，直接回车跳过 [Y/N]' }
            if ($pgvAns -match '^(y|yes)$') {
                try {
                    & "$PSScriptRoot\install_pgvector.ps1"
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host '       [提示] pgvector 安装未成功完成，可稍后单独双击 install_pgvector.bat 重试。'
                    }
                } catch {
                    Write-Host "       [提示] pgvector 安装过程出错：$($_.Exception.Message)"
                    Write-Host '             可稍后单独双击 install_pgvector.bat 重试。'
                }
            } else {
                Write-Host '       已跳过。稍后可双击 install_pgvector.bat 单独安装。'
                $pgvSkipped = $true
            }
        } else {
            Write-Host '       注意：安装 pgvector 需要写入 PostgreSQL 安装目录，需要管理员权限。'
            $pgvAns = Read-Host '       输入 Y 现在以管理员身份继续安装；直接回车先跳过 [Y/N]'
            if ($pgvAns -match '^(y|yes)$') {
                $InstallPgvector = $true
                $code = Start-ElevatedSetupChild
                if ($code -eq 0) { exit 0 }
                if ($code -eq 2) {
                    # 用户取消提权：pgvector 是可选步骤，别因此中断整个安装
                    Write-Host '       [提示] 未能获得管理员权限，本次先跳过 pgvector，继续安装。'
                    $pgvSkipped = $true
                } else {
                    exit $code
                }
            } else {
                Write-Host '       已跳过（当前没有管理员权限）。稍后可双击 install_pgvector.bat 单独安装。'
                $pgvSkipped = $true
            }
        }
    }
}

# ---------- 6. 让 PostgreSQL 服务开机自启（无 PostgreSQL 则跳过）----------
# 部分用户的 PostgreSQL Windows 服务启动类型是「手动」，开机不会自动启动，
# 进入程序的会话界面就会连不上数据库。这里把检测到的 PostgreSQL 服务设为「自动」
# 并立即启动。修改服务配置需要管理员：普通权限下只检测并给出提示，不阻断安装
#（运行期程序还会兜底拉起服务，见 app/services/pg_service.py）。
Write-Host '[6/8] 配置 PostgreSQL 服务开机自启...'
$pgServiceNeedsAdmin = $false
try {
    $pgServices = @(Get-CimInstance -ClassName Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'postgresql*' -or $_.Name -like 'pgsql*' -or $_.DisplayName -like '*PostgreSQL*' })
    if ($pgServices.Count -eq 0) {
        Write-Host '       未检测到 PostgreSQL 服务，跳过（安装 PostgreSQL 后程序会在启动时自动拉起）。'
    } else {
        foreach ($svc in $pgServices) {
            try {
                if ($svc.StartMode -ne 'Auto') {
                    if (-not $isAdmin) {
                        $pgServiceNeedsAdmin = $true
                        Write-Host "       [提示] $($svc.Name) 当前不是「自动」启动；修改服务配置需要管理员权限，已跳过。"
                        continue
                    }
                    Set-Service -Name $svc.Name -StartupType Automatic -ErrorAction Stop
                    Write-Host "       已将 $($svc.Name) 的启动类型设为「自动」。"
                } else {
                    Write-Host "       $($svc.Name) 已是「自动」启动。"
                }
                $cur = Get-Service -Name $svc.Name -ErrorAction SilentlyContinue
                if ($cur -and $cur.Status -ne 'Running') {
                    if (-not $isAdmin) {
                        $pgServiceNeedsAdmin = $true
                        Write-Host "       [提示] $($svc.Name) 当前未运行；启动服务需要管理员权限，已跳过。"
                        continue
                    }
                    Start-Service -Name $svc.Name -ErrorAction Stop
                    Write-Host "       已启动 $($svc.Name)。"
                }
            } catch {
                Write-Host "       [提示] 处理 $($svc.Name) 失败：$($_.Exception.Message)"
            }
        }
    }
} catch {
    Write-Host "       [警告] 配置 PostgreSQL 服务自启失败：$($_.Exception.Message)"
}
if ($pgServiceNeedsAdmin) {
    Write-Host '       [提示] 未以管理员运行，服务启动类型/运行状态未修改。'
    Write-Host '             程序启动时仍会自动拉起服务（可能弹一次 UAC）。'
    Write-Host '             如需开机自启，请稍后右键 setup.bat 选择「以管理员身份运行」。'
}

# ---------- 7. 打包 exe ----------
Write-Host "[7/8] 打包 $exeName（首次会自动安装打包工具）..."
$buildScript = if ($Mode -eq 'Lite') {
    Join-Path $PSScriptRoot 'lite\build_lite.py'
} else {
    Join-Path $PSScriptRoot 'build_exe.py'
}
& "$PSScriptRoot\venv\Scripts\python.exe" $buildScript
if ($LASTEXITCODE -ne 0) {
    Stop-InstallStep -Message '[错误] 打包 exe 失败，请截图上方完整报错信息反馈。' -Reason '打包 exe 失败（可能是权限或长路径限制）'
}

# ---------- 8. 创建桌面快捷方式 ----------
# 关键：如果提权后的账户与提权前记录的用户不同（例如自己不是管理员，UAC 里输入了
# 另一个管理员账户），直接创建会落到管理员账户的桌面。此时只写交接标记，
# 等本安装进程退出（退出提权）后，由仍在原用户上下文中的父进程创建。
Write-Host '[8/8] 处理桌面快捷方式...'
$runIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$runUserName = $runIdentity.Name
$runUserSid = $runIdentity.User.Value
$sameUser = [string]::IsNullOrWhiteSpace($OriginalSid) -or ($OriginalSid -eq $runUserSid)
$shortcutCreated = $false

if ($sameUser) {
    try {
        $lnkPath = New-StudioDesktopShortcut -ExeName $exeName
        $shortcutCreated = $true
        Write-Host "       已创建桌面快捷方式：$lnkPath"
    } catch {
        Write-Host "       [警告] 创建桌面快捷方式失败：$($_.Exception.Message)"
        Write-Host "             可手动右键 $exeName → 发送到 → 桌面快捷方式，并重命名为「MultiAgent Studio」。"
    }
} else {
    Write-Host '       提权后的账户与提权前登录用户不同：'
    Write-Host "         提权前登录用户：$OriginalUser"
    Write-Host "         提权后管理员账户：$runUserName"
    Write-Host '       已记录交接标记；本安装窗口退出后，会回到原登录用户桌面创建快捷方式。'
    try {
        Set-Content -LiteralPath $pendingShortcutMarker -Value $exeName -Encoding ASCII
    } catch {
        Write-Host "       [警告] 写入快捷方式交接标记失败：$($_.Exception.Message)"
        Write-Host "             请安装结束后手动右键 $exeName → 发送到 → 桌面快捷方式。"
    }
}

Write-Host ''
Write-Host '================================================'
Write-Host "  安装完成！已生成 $exeName（$modeLabel）。"
if ($sameUser) {
    if ($shortcutCreated) {
        Write-Host '  桌面快捷方式「MultiAgent Studio」已创建。'
        Write-Host '  现在双击它即可启动。'
    } else {
        Write-Host '  桌面快捷方式创建失败，请按上方提示手动创建。'
    }
} else {
    Write-Host '  桌面快捷方式将在本安装窗口关闭后，由原登录用户窗口创建。'
}
Write-Host '================================================'
Write-Host ''

if ($pgvSkipped) {
    Write-Host '提示：本次未安装 pgvector，长期记忆功能会不可用；'
    Write-Host '      可随时双击 install_pgvector.bat 单独安装。'
    Write-Host ''
}
if (-not $longPathsEnabled) {
    Write-Host '提示：Windows 长路径支持尚未启用（修改它需要管理员权限）。'
    Write-Host '      本次安装已完成；若以后遇到「路径过长」报错，'
    Write-Host '      请右键 setup.bat 选择「以管理员身份运行」即可自动启用。'
    Write-Host ''
}
