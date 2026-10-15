# DeepQuant 多终端同步脚本（Windows PowerShell）。
#
# 用法：
#   .\scripts\git-sync.ps1 "提交说明"     # 省略说明时使用时间戳
#
# 行为：git add -A → 有改动则 commit → git pull --rebase --autostash → git push
# 安全约定：绝不 force push；rebase 出现冲突时停止，需人工解决后再运行。
param(
    [string]$Message = ("sync: " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
)

$ErrorActionPreference = 'Stop'

# 切到仓库根目录（脚本位于 scripts/ 下）
Set-Location (Join-Path $PSScriptRoot '..')

git add -A
if ($LASTEXITCODE -ne 0) { throw "[git-sync] git add 失败" }

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { throw "[git-sync] git commit 失败" }
} else {
    Write-Host "[git-sync] 无变更需要提交"
}

git pull --rebase --autostash
if ($LASTEXITCODE -ne 0) {
    throw "[git-sync] git pull --rebase 失败，请手动解决冲突后再运行"
}

git push
if ($LASTEXITCODE -ne 0) {
    throw "[git-sync] git push 失败，请检查网络/认证（gh auth status）"
}

Write-Host "[git-sync] 已同步到远端"
