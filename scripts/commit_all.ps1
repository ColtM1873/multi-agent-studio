# 一次跑完「内部文档哈希清单 + 两个仓库提交」。
#
# 编码：本文件必须保留 UTF-8 BOM（Windows PowerShell 5.1 需要它才能正确解析中文）。
#       用编辑器/脚本改写后，请确认首字节是 EF BB BF；丢了 BOM 会报一堆语法错误。
#
# 背景：DSH 的 shell 沙箱禁止写 inner_docs/ 与 .git/，所以在 DSH 会话里必须用一条
# 提权命令执行本脚本（sandbox_permissions: danger-full-access），一次审批完成全部写盘。
# 普通终端也可直接运行。
#
# 用法：
#   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\commit_all.ps1 -DryRun
#   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\commit_all.ps1 `
#       -DocsMessage "docs: add ID83 ..." `
#       -MainMessage "feat: ..." `
#       -MainPaths app\static\js\app.js, app\main.py
#
# 在 DSH 里（提权执行同一条命令）：
#   & 'scripts\commit_all.ps1' -DocsMessage "..." -MainMessage "..."
#
# 退出码：0 = 全部成功（或没有可提交内容）；1 = 任一步失败，脚本立即停止。

[CmdletBinding()]
param(
    # 内部文档仓库的提交信息（默认占位，建议显式传入）
    [string]$DocsMessage = "docs: update inner docs",
    # 主仓库的提交信息（默认占位，建议显式传入）
    [string]$MainMessage = "chore: update project files",
    # 只暂存这些路径（相对仓库根）；不传则 git add -A
    [string[]]$MainPaths = @(),
    # 跳过内部文档仓库（只提交主仓库）
    [switch]$SkipDocs,
    # 跳过主仓库（只提交内部文档）
    [switch]$SkipMain,
    # 只读预检：打印待提交内容、校验哈希清单，不写任何文件、不提交（无需提权）
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# powershell.exe -File 传数组时，值会被压成「逗号拼接的单个字符串」（如 "a,b"）；
# 这里统一拆成数组，保证 -MainPaths 在 -File 与 -Command 两种调用方式下行为一致。
# 注意：路径本身不要含逗号。
$MainPaths = @(
    $MainPaths |
        ForEach-Object { $_ -split ',' } |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)

$Root = Split-Path -Parent $PSScriptRoot
$DocsRepo = Join-Path $Root 'inner_docs'
$ManifestTool = Join-Path $DocsRepo 'tools\doc_hashes.py'
$Python = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }

function Write-Step([string]$Text) {
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "[ok] $Text" -ForegroundColor Green
}

function Fail([string]$Text) {
    Write-Host "[fail] $Text" -ForegroundColor Red
    exit 1
}

function Test-GitRepo([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { Fail ("未找到{0}：{1}" -f $Label, $Path) }
    & git -C $Path rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) { Fail ("{0}不是 git 仓库：{1}" -f $Label, $Path) }
}

function Test-HasChanges([string]$Path) {
    $out = & git -C $Path status --porcelain
    if ($LASTEXITCODE -ne 0) { Fail ("无法读取 git 状态：{0}" -f $Path) }
    return [bool](@($out) | Where-Object { $_ -and $_.Trim() })
}

function Commit-IfStaged([string]$Path, [string]$Message, [string]$Label) {
    & git -C $Path diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Ok ("{0}暂存区无变化，跳过提交" -f $Label)
        return
    }
    & git -C $Path commit -m $Message
    if ($LASTEXITCODE -ne 0) { Fail ("git commit 失败（{0}）" -f $Label) }
    $head = (& git -C $Path log --oneline -1)
    Write-Ok ("{0}已提交：{1}" -f $Label, $head)
}

Write-Step ("仓库根：{0}" -f $Root)
if ($DryRun) { Write-Host "（DryRun：只读预检，不会写文件、不会提交）" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 内部文档仓库
if (-not $SkipDocs) {
    Test-GitRepo $DocsRepo '内部文档仓库'
    if (-not (Test-Path -LiteralPath $ManifestTool)) {
        Fail ("未找到哈希清单脚本：{0}" -f $ManifestTool)
    }

    Write-Step '检查内部文档仓库改动'
    if (-not (Test-HasChanges $DocsRepo)) {
        Write-Ok '内部文档无改动，跳过清单重算与提交'
    }
    elseif ($DryRun) {
        & git -C $DocsRepo status --short
        Write-Step '校验现有哈希清单（DryRun 不重算）'
        & $Python $ManifestTool --check
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[warn] 哈希清单与当前文件不一致（文档有改动、清单尚未重算；正式运行会自动重算）' -ForegroundColor Yellow
        }
        else {
            Write-Ok '哈希清单当前一致'
        }
        Write-Ok 'DryRun：以上为内部文档待提交内容'
    }
    else {
        Write-Step '重新生成哈希清单'
        & $Python $ManifestTool
        if ($LASTEXITCODE -ne 0) { Fail '生成哈希清单失败（HASH_MANIFEST.sha256 写入被拒？）' }

        Write-Step '校验哈希清单'
        & $Python $ManifestTool --check
        if ($LASTEXITCODE -ne 0) { Fail '哈希清单校验失败（清单与文件不一致）' }

        Write-Step '暂存并提交内部文档仓库'
        & git -C $DocsRepo add -A
        if ($LASTEXITCODE -ne 0) { Fail 'git add 失败（内部文档仓库）' }
        Commit-IfStaged $DocsRepo $DocsMessage '内部文档仓库'
    }
}

# ------------------------------------------------------------------ 主仓库
if (-not $SkipMain) {
    Test-GitRepo $Root '主仓库'

    Write-Step '检查主仓库改动'
    if (-not (Test-HasChanges $Root)) {
        Write-Ok '主仓库无改动，跳过提交'
    }
    elseif ($DryRun) {
        & git -C $Root status --short
        Write-Ok 'DryRun：以上为主仓库待提交内容'
    }
    else {
        Write-Step '暂存主仓库改动'
        if ($MainPaths.Count -gt 0) {
            & git -C $Root add -- $MainPaths
        }
        else {
            & git -C $Root add -A
        }
        if ($LASTEXITCODE -ne 0) { Fail 'git add 失败（主仓库）' }
        Commit-IfStaged $Root $MainMessage '主仓库'
    }
}

Write-Step '完成'
Write-Host '工作区状态：'
& git -C $Root status --porcelain
if (-not $SkipDocs) { & git -C $DocsRepo status --porcelain }

if ($DryRun) {
    Write-Ok 'DryRun 结束（未做任何写入）'
}
else {
    Write-Ok '全部完成'
}
exit 0
