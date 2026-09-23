# Multi-Agent Studio · PostgreSQL pgvector 一键安装脚本
# 由 install_pgvector.bat 调用；也可在 setup.ps1 里被调用。
#
# 目标：一键把 pgvector 文件装到 PostgreSQL 安装目录，客户无需打开 psql、
#       也无需手敲 CREATE EXTENSION vector（程序会在首次聊天时自动创建）。
#
# 流程：提权 → 识别 PostgreSQL 根目录 → 识别 Visual Studio C++ 编译环境
#       → 优先从官方源码编译安装 → 无编译环境/编译失败时回退到预编译包（带风险提示 + sha256 校验）
#       → 校验安装结果。
#
# 参数：
#   -UsePrebuilt  跳过源码编译，直接使用预编译包
#   -AssumeYes    回退到预编译包时不再询问确认（自动化用）
#   -Status       仅检测模式：非交互、不下载、不提权，只判断 PostgreSQL 与 pgvector 状态后退出。
#                 供 setup.ps1 决定「是否需要询问安装」。退出码：0=已装；1=有 PG 但未装；2=未找到 PG。

[CmdletBinding()]
param(
    [switch]$UsePrebuilt,
    [switch]$AssumeYes,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

# ---------- 常量 ----------
$PgVectorVersion = '0.8.6'
$SourceZipUrl = "https://github.com/pgvector/pgvector/archive/refs/tags/v$PgVectorVersion.zip"

# 下载源：第一个为空串表示直连 GitHub，其余为镜像前缀（失败后依次尝试）
$MirrorPrefixes = @(
    '',
    'https://ghfast.top/',
    'https://gh-proxy.com/',
    'https://mirror.ghproxy.com/'
)

# 预编译回退：社区非官方仓库（andreiramani/pgvector_pgsql_windows），
# 锁定的 sha256 摘要（pgvector v0.8.6）。
$PrebuiltDigests = @{
    '13' = 'd80bc89dca13ef25204b551f68daff05ad60f13bb34a4e892d4472b50cc62262'
    '14' = 'b32a743b01e5c178708197f7b8e5fe1da64566b52fefaf3e0f6e26c560ee561c'
    '15' = 'ac109a074654faf03e5b989963ed855f9acb0ceb5953c4ab8a285212800d1c19'
    '16' = 'faeaecb100488397ce5d38424b8c931be0f29e1668be7d4e75c26fe2eb056522'
    '17' = '420388e9e9f05d92f06d6967ce8772483629b27a66ca9255925fa0fdd445438e'
    '18' = 'bda17eb97d9e687e3da701adbf4b65a342943b3e0cdc81935ccf0b9833a1ed62'
}
$PrebuiltRepo = 'andreiramani/pgvector_pgsql_windows'

# ---------- 工具函数 ----------
function Write-Info { param([string]$Msg) Write-Host $Msg }

function Download-FileWithMirror {
    param([string]$Url, [string]$Destination)
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        foreach ($prefix in $MirrorPrefixes) {
            $tryUrl = $prefix + $Url
            if ($prefix -ne '') { Write-Info "       尝试镜像：$prefix" }
            try {
                if (Test-Path $Destination) { Remove-Item $Destination -Force -ErrorAction SilentlyContinue }
                Invoke-WebRequest -Uri $tryUrl -OutFile $Destination -UseBasicParsing -TimeoutSec 300
                if ((Test-Path $Destination) -and ((Get-Item $Destination).Length -gt 0)) {
                    return
                }
            } catch {
                Write-Info "       下载失败：$tryUrl"
            }
        }
        throw "所有下载源均失败：$Url"
    } finally {
        $ProgressPreference = $oldProgress
    }
}

function Get-PgRootCandidates {
    $cands = New-Object System.Collections.Generic.List[string]

    # 1) 注册表（EDB 官方安装器会写这里）
    foreach ($regRoot in @(
            'HKLM:\SOFTWARE\PostgreSQL\Installations',
            'HKLM:\SOFTWARE\WOW6432Node\PostgreSQL\Installations')) {
        if (Test-Path $regRoot) {
            Get-ChildItem $regRoot -ErrorAction SilentlyContinue | ForEach-Object {
                $base = (Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue).'Base Directory'
                if ($base) { $cands.Add($base) }
            }
        }
    }

    # 2) Windows 服务（postgresql-x64-<ver>）
    try {
        Get-CimInstance -ClassName Win32_Service -Filter "Name LIKE 'postgresql%'" -ErrorAction SilentlyContinue | ForEach-Object {
            $pn = $_.PathName
            if ($pn) {
                if ($pn -match '^\s*"([^"]+)"') { $exe = $Matches[1] }
                else { $exe = ($pn -split '\s+')[0] }
                if ($exe) {
                    $binDir = Split-Path -Path $exe -Parent
                    if ($binDir) { $cands.Add((Split-Path -Path $binDir -Parent)) }
                }
            }
        }
    } catch { }

    # 3) 常见安装目录
    foreach ($base in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $pgParent = Join-Path $base 'PostgreSQL'
        if (Test-Path $pgParent) {
            Get-ChildItem $pgParent -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $cands.Add($_.FullName)
            }
        }
    }

    return ($cands | Where-Object { $_ } | Select-Object -Unique)
}

function Test-PgRoot {
    param([string]$Root)
    if (-not $Root) { return $false }
    return (Test-Path (Join-Path $Root 'bin\pg_config.exe')) -and (Test-Path (Join-Path $Root 'share\extension'))
}

function Get-PgMajor {
    param([string]$Root)
    $pgConfig = Join-Path $Root 'bin\pg_config.exe'
    if (Test-Path $pgConfig) {
        try {
            $out = & $pgConfig --version 2>$null
            if ($out -match 'PostgreSQL\s+(\d+)') { return $Matches[1] }
        } catch { }
    }
    if ((Split-Path -Leaf $Root) -match '^(\d+)$') { return $Matches[1] }
    return $null
}

function Resolve-PgRoot {
    $cands = @(Get-PgRootCandidates | Where-Object { Test-PgRoot $_ })

    if ($cands.Count -eq 1) {
        Write-Info "       已自动识别：$($cands[0])"
        return $cands[0]
    }

    if ($cands.Count -gt 1) {
        Write-Info '       检测到多个 PostgreSQL 安装目录：'
        for ($i = 0; $i -lt $cands.Count; $i++) {
            Write-Info ("         [{0}] {1}" -f ($i + 1), $cands[$i])
        }
        while ($true) {
            $sel = Read-Host '       请选择序号'
            $n = 0
            if ([int]::TryParse($sel, [ref]$n) -and $n -ge 1 -and $n -le $cands.Count) {
                return $cands[$n - 1]
            }
            Write-Info '       输入无效，请重新输入。'
        }
    }

    Write-Info '       未能自动识别 PostgreSQL 安装目录。'
    Write-Info '       请填写 PostgreSQL 根目录（包含 bin、lib、share 的那一层，例如 C:\Program Files\PostgreSQL\18）。'
    while ($true) {
        $manual = (Read-Host '       请输入路径').Trim().Trim('"').Trim()
        if (Test-PgRoot $manual) { return $manual }
        Write-Info '       该路径下未找到 bin\pg_config.exe 或 share\extension，请确认后重试。'
    }
}

function Get-VsVcvars {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { return $null }

    $queries = @(
        @('-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'),
        @('-products', '*', '-property', 'installationPath')
    )
    foreach ($q in $queries) {
        try {
            $paths = & $vswhere @q 2>$null
        } catch {
            $paths = $null
        }
        if ($paths) {
            foreach ($p in @($paths)) {
                $p = "$p".Trim()
                if (-not $p) { continue }
                $vcvars = Join-Path $p 'VC\Auxiliary\Build\vcvars64.bat'
                if (Test-Path $vcvars) { return $vcvars }
            }
        }
    }
    return $null
}

function Invoke-SourceBuild {
    param([string]$PgRoot, [string]$Vcvars)
    $work = Join-Path $env:TEMP ("pgvector_src_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $zip = Join-Path $work 'pgvector.zip'
        Write-Info "       正在下载 pgvector 官方源码 v$PgVectorVersion ..."
        Download-FileWithMirror -Url $SourceZipUrl -Destination $zip

        Write-Info '       正在解压源码...'
        Expand-Archive -Path $zip -DestinationPath $work -Force
        $srcDir = Get-ChildItem -Path $work -Directory | Where-Object { $_.Name -like 'pgvector-*' } | Select-Object -First 1
        if (-not $srcDir) { throw '源码解压后未找到 pgvector 目录。' }

        # 生成临时 bat，避免 Start-Process / 引号转义问题
        $bat = Join-Path $work 'build_pgvector.bat'
        $lines = @(
            '@echo off',
            "call `"$Vcvars`" >nul 2>&1",
            'where nmake >nul 2>&1 || exit /b 3',
            "set `"PGROOT=$PgRoot`"",
            "cd /d `"$($srcDir.FullName)`"",
            'nmake /F Makefile.win || exit /b 1',
            'nmake /F Makefile.win install || exit /b 2'
        )
        [System.IO.File]::WriteAllText($bat, ($lines -join "`r`n"), [System.Text.Encoding]::Default)

        Write-Info '       正在编译并安装（nmake，可能需要一两分钟）...'
        & cmd.exe /c $bat
        $code = $LASTEXITCODE
        switch ($code) {
            0 { return }
            3 { throw '在 vcvars64 环境中未找到 nmake，请确认 Visual Studio 已安装「使用 C++ 的桌面开发」工作负载。' }
            1 { throw 'nmake 编译失败。若是 PostgreSQL 17.0–17.2，请升级到 17.3+ 后重试。' }
            2 { throw 'nmake install 失败（可能权限不足或 PGROOT 不正确）。' }
            default { throw "nmake 执行失败（退出码 $code）。" }
        }
    } finally {
        Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Copy-PgVectorFiles {
    param([string]$ExtractDir, [string]$PgRoot)
    $copied = $false

    # 优先按 PG 目录结构（lib/share/include/bin）合并复制
    foreach ($sub in @('lib', 'share', 'include', 'bin')) {
        $dirs = Get-ChildItem -Path $ExtractDir -Directory -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ieq $sub }
        foreach ($d in $dirs) {
            $target = Join-Path $PgRoot $sub
            if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
            Copy-Item -Path (Join-Path $d.FullName '*') -Destination $target -Recurse -Force -ErrorAction SilentlyContinue
            $copied = $true
        }
    }

    # 扁平布局兜底：按文件类型放置
    if (-not $copied) {
        $dll = Get-ChildItem -Path $ExtractDir -Filter 'vector.dll' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($dll) { Copy-Item $dll.FullName (Join-Path $PgRoot 'lib') -Force; $copied = $true }

        $control = Get-ChildItem -Path $ExtractDir -Filter 'vector.control' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($control) { Copy-Item $control.FullName (Join-Path $PgRoot 'share\extension') -Force; $copied = $true }

        Get-ChildItem -Path $ExtractDir -Filter 'vector--*.sql' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $PgRoot 'share\extension') -Force
            $copied = $true
        }

        $headers = Get-ChildItem -Path $ExtractDir -Filter 'vector*.h' -Recurse -ErrorAction SilentlyContinue
        if ($headers) {
            $incDir = Join-Path $PgRoot 'include\server\extension\vector'
            if (-not (Test-Path $incDir)) { New-Item -ItemType Directory -Path $incDir -Force | Out-Null }
            $headers | ForEach-Object { Copy-Item $_.FullName $incDir -Force }
            $copied = $true
        }
    }

    if (-not $copied) { throw '解压后未找到可安装的 pgvector 文件。' }
}

function Install-Prebuilt {
    param([string]$PgRoot, [string]$Major, [switch]$AutoConfirm)

    if (-not $PrebuiltDigests.ContainsKey($Major)) {
        throw "暂无 PostgreSQL $Major 对应的预编译包（仅支持 13–18）。"
    }

    Write-Info ''
    Write-Info '       ================ 风险提示 ================'
    Write-Info '       回退方案使用的是社区「非官方」预编译二进制'
    Write-Info '       （andreiramani/pgvector_pgsql_windows）。'
    Write-Info '       它无法像官方源码那样逐行审计，存在潜在供应链风险。'
    Write-Info '       脚本会用锁定的 sha256 校验文件完整性，但无法替代来源审计。'
    Write-Info '       如条件允许，建议安装 Visual Studio 的 C++ 组件后改用官方源码编译。'
    Write-Info '       ========================================='
    Write-Info ''

    if (-not $AutoConfirm) {
        $ans = Read-Host '       确认使用预编译包继续吗？输入 YES 继续，其它内容取消'
        if ($ans -ne 'YES') { throw '用户取消了预编译回退安装。' }
    }

    $tag = "$PgVectorVersion`_$Major"
    $asset = "vector.v$PgVectorVersion-pg$Major.zip"
    $url = "https://github.com/$PrebuiltRepo/releases/download/$tag/$asset"

    $work = Join-Path $env:TEMP ("pgvector_bin_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    try {
        $zip = Join-Path $work $asset
        Write-Info "       正在下载预编译包 $asset ..."
        Download-FileWithMirror -Url $url -Destination $zip

        $expected = $PrebuiltDigests[$Major].ToLower()
        $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $expected) {
            throw "sha256 校验失败，已中止安装。`n       期望：$expected`n       实际：$actual"
        }
        Write-Info '       sha256 校验通过。'

        $extract = Join-Path $work 'extract'
        Expand-Archive -Path $zip -DestinationPath $extract -Force
        Copy-PgVectorFiles -ExtractDir $extract -PgRoot $PgRoot
        Write-Info '       预编译文件已复制到 PostgreSQL 安装目录。'
    } finally {
        Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Show-InstallResult {
    param([string]$PgRoot)
    $control = Join-Path $PgRoot 'share\extension\vector.control'
    $dll = Join-Path $PgRoot 'lib\vector.dll'
    $sql = Get-ChildItem (Join-Path $PgRoot 'share\extension') -Filter 'vector--*.sql' -ErrorAction SilentlyContinue

    $okControl = Test-Path $control
    $okDll = Test-Path $dll
    $okSql = [bool]$sql

    Write-Info ("       share\extension\vector.control : " + $(if ($okControl) { '存在' } else { '缺失' }))
    Write-Info ("       lib\vector.dll                 : " + $(if ($okDll) { '存在' } else { '缺失' }))
    Write-Info ("       share\extension\vector--*.sql  : " + $(if ($okSql) { '存在' } else { '缺失' }))

    return ($okControl -and $okDll -and $okSql)
}

# ---------- 仅检测模式（供 setup.ps1 判断是否需要询问安装）----------
# 非交互、不下载、不提权：只判定「能否找到 PostgreSQL」「是否已装 pgvector」。
# 退出码：0 = 已安装；1 = 找到 PostgreSQL 但未安装；2 = 未找到 PostgreSQL。
if ($Status) {
    $cands = @(Get-PgRootCandidates | Where-Object { Test-PgRoot $_ })
    if (-not $cands) {
        Write-Host '[检测] 未找到 PostgreSQL 安装目录。'
        exit 2
    }
    $installedRoots = @($cands | Where-Object {
        Test-Path (Join-Path $_ 'share\extension\vector.control')
    })
    if ($installedRoots.Count -gt 0) {
        Write-Host "[检测] pgvector 已安装：$($installedRoots[0])"
        exit 0
    }
    Write-Host "[检测] 已找到 PostgreSQL，但未检测到 pgvector：$($cands[0])"
    exit 1
}

# ---------- 提权 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "安装 pgvector 需要写入 PostgreSQL 安装目录，需要管理员权限。`n`n点击「确定」将尝试以管理员身份重新启动。`n若未弹出「用户账户控制」窗口，请关闭本窗口后，右键 install_pgvector.bat 选择「以管理员身份运行」。",
        'pgvector 一键安装', 'OK', 'Information') | Out-Null
    $argList = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`""
    if ($UsePrebuilt) { $argList += ' -UsePrebuilt' }
    if ($AssumeYes) { $argList += ' -AssumeYes' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs
    } catch {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            '自动提权失败，请右键 install_pgvector.bat 选择「以管理员身份运行」。',
            'pgvector 一键安装', 'OK', 'Warning') | Out-Null
    }
    exit
}

# ---------- 主流程 ----------
Write-Host ''
Write-Host '================================================'
Write-Host '  PostgreSQL pgvector 一键安装'
Write-Host '================================================'
Write-Host ''

try {
    Write-Host '[1/4] 识别 PostgreSQL 安装目录...'
    $pgRoot = Resolve-PgRoot
    $major = Get-PgMajor $pgRoot
    if (-not $major) {
        $major = (Read-Host '       无法确定 PostgreSQL 主版本号，请手动输入（如 18）').Trim()
    }
    Write-Host "       PostgreSQL 目录：$pgRoot（主版本 $major）"

    if (Test-Path (Join-Path $pgRoot 'share\extension\vector.control')) {
        Write-Host ''
        Write-Host '[完成] 检测到 pgvector 已安装，无需重复安装。'
        Write-Host '       重启本程序后，程序会在首次聊天时自动执行 CREATE EXTENSION vector。'
        Write-Host ''
        exit 0
    }

    $vcvars = $null
    if (-not $UsePrebuilt) {
        Write-Host '[2/4] 查找 Visual Studio C++ 编译环境...'
        $vcvars = Get-VsVcvars
        if ($vcvars) {
            Write-Host "       已找到：$vcvars"
        } else {
            Write-Host '       未找到可用的 Visual Studio C++ 编译环境。'
            Write-Host '       如需官方源码编译，请安装 Visual Studio Community 并勾选「使用 C++ 的桌面开发」：'
            Write-Host '         https://visualstudio.microsoft.com/zh-hans/vs/community/'
        }
    } else {
        Write-Host '[2/4] 已指定使用预编译包，跳过编译器查找。'
    }

    $sourceOk = $false
    if ($vcvars) {
        Write-Host '[3/4] 从官方源码编译安装 pgvector...'
        try {
            Invoke-SourceBuild -PgRoot $pgRoot -Vcvars $vcvars
            $sourceOk = $true
            Write-Host '       源码编译安装完成。'
        } catch {
            Write-Host ''
            Write-Host "       [警告] 源码编译失败：$($_.Exception.Message)"
            Write-Host '       将尝试回退到预编译包。'
        }
    } else {
        Write-Host '[3/4] 跳过源码编译（无编译环境）。'
    }

    if (-not $sourceOk) {
        Write-Host '[4/4] 使用预编译包安装 pgvector...'
        Install-Prebuilt -PgRoot $pgRoot -Major $major -AutoConfirm:$AssumeYes
    }

    Write-Host ''
    Write-Host '[校验] 检查安装结果...'
    if (-not (Show-InstallResult -PgRoot $pgRoot)) {
        throw '安装后仍未找到完整文件。'
    }

    Write-Host ''
    Write-Host '================================================'
    Write-Host '  pgvector 安装完成！'
    Write-Host '  无需重启 PostgreSQL，也无需手动执行 CREATE EXTENSION。'
    Write-Host '  重启本程序后，程序会在首次聊天时自动创建该扩展。'
    Write-Host '================================================'
    Write-Host ''
} catch {
    Write-Host ''
    Write-Host "[错误] pgvector 安装失败：$($_.Exception.Message)"
    Write-Host '       可稍后双击 install_pgvector.bat 重试，或截图上方完整信息反馈。'
    Write-Host ''
    exit 1
}

exit 0
