<#
    One-command installer entry point - meant to be run via:
      irm https://raw.githubusercontent.com/Osaka-Research/minimax-h3-windows/main/bootstrap.ps1 | iex
    Fetches the repo into %USERPROFILE%\minimax-h3-windows and runs install.ps1.
    Deliberately assumes nothing about the host beyond PowerShell itself -
    doesn't even require git (downloads a zip instead if git isn't present
    yet; install.ps1 installs git properly afterward, since it needs it
    anyway to clone ComfyUI). For anything beyond the default full install
    (e.g. -SkipAutostart), clone the repo yourself and run install.ps1
    directly instead.
#>

$ErrorActionPreference = "Stop"

# Fresh Windows installs typically default to a PowerShell execution policy
# that blocks running .ps1 FILES (this script itself is fine - it's piped
# into iex, not run as a file - but the install.ps1 file we invoke below
# would otherwise be blocked). Process scope, no admin needed.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$repoUrl = "https://github.com/Osaka-Research/minimax-h3-windows.git"
$zipUrl = "https://github.com/Osaka-Research/minimax-h3-windows/archive/refs/heads/main.zip"
$target = Join-Path $env:USERPROFILE "minimax-h3-windows"

if (Test-Path (Join-Path $target ".git")) {
    Write-Host "== $target already exists - pulling latest instead of cloning ==" -ForegroundColor Cyan
    Push-Location $target
    git pull
    Pop-Location
} elseif (Test-Path $target) {
    Write-Host "== $target already exists (from a previous zip-based fetch, no git yet at the time) - leaving it as-is ==" -ForegroundColor Cyan
    Write-Host "Delete that folder and re-run this command if you want a fresh fetch now that git is available."
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "== Cloning into $target ==" -ForegroundColor Cyan
    git clone $repoUrl $target
} else {
    # No git yet - don't require it just to fetch this repo. install.ps1
    # installs git properly afterward (it needs it anyway, to clone ComfyUI).
    Write-Host "== git not found yet - downloading a zip snapshot instead ==" -ForegroundColor Cyan
    $zipPath = Join-Path $env:TEMP "minimax-h3-windows.zip"
    $extractDir = Join-Path $env:TEMP "minimax-h3-windows-extract"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    # GitHub's zip archives extract to a "<repo>-<branch>" folder - must match the repo name exactly.
    Move-Item (Join-Path $extractDir "minimax-h3-windows-main") $target
    Remove-Item $zipPath, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Location $target
Write-Host "== Running install.ps1 ==" -ForegroundColor Cyan
& (Join-Path $target "install.ps1")
