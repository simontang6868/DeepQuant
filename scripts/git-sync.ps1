# DeepQuant 多终端同步脚本（Windows PowerShell）。
#
# 用法：
#   .\scripts\git-sync.ps1 "提交说明"     # 省略说明时使用时间戳
#
# 行为：git add -A → 有改动则 commit → git pull --rebase --autostash → git push
# 认证：若本机已安装并登录 gh，则让 git 复用 gh 凭据（无需把令牌写入任何文件）；
#       否则使用本机已配置的凭据助手（如 Git Credential Manager）。
# 安全：绝不 force push；rebase 出现冲突时停止，需人工解决后再运行。
param(
    [string]$Message = ("sync: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
)

$ErrorActionPreference = 'Stop'

# 切到仓库根目录（脚本位于 scripts/ 下）
Set-Location (Join-Path $PSScriptRoot '..')

# 认证：优先复用已登录的 gh 凭据（仅本次命令，不持久化配置）
$gitAuth = @()
if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status *> $null
    if ($LASTEXITCODE -eq 0) {
        $gitAuth = @('-c', 'credential.helper=!gh auth git-credential')
    }
}

git add -A
if ($LASTEXITCODE -ne 0) { throw "[git-sync] git add 失败" }

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { throw "[git-sync] git commit 失败" }
} else {
    Write-Host "[git-sync] 无变更需要提交"
}

git @gitAuth pull --rebase --autostash
if ($LASTEXITCODE -ne 0) {
    throw "[git-sync] git pull --rebase 失败：如有冲突请手动解决后重试；如为认证失败，请先运行 'gh auth login'"
}

git @gitAuth push
if ($LASTEXITCODE -ne 0) {
    throw "[git-sync] git push 失败：请检查网络与 GitHub 认证（gh auth status）"
}

Write-Host "[git-sync] 已同步到远端"
