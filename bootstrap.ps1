<#
    One-command installer entry point - meant to be run via:
      irm https://raw.githubusercontent.com/Osaka-Research/video-gen/main/bootstrap.ps1 | iex
    Clones (or updates) the repo into %USERPROFILE%\video-gen and runs install.ps1.
    For anything beyond the default full install (e.g. -SkipAutostart), clone the
    repo yourself and run install.ps1 directly instead.
#>

$ErrorActionPreference = "Stop"

# Fresh Windows installs typically default to a PowerShell execution policy
# that blocks running .ps1 FILES (this script itself is fine - it's piped
# into iex, not run as a file - but the install.ps1 file we invoke below
# would otherwise be blocked). Process scope, no admin needed.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git not found on PATH. Install Git from https://git-scm.com/download/win and re-run this command."
    exit 1
}

$repoUrl = "https://github.com/Osaka-Research/video-gen.git"
$target = Join-Path $env:USERPROFILE "video-gen"

if (Test-Path (Join-Path $target ".git")) {
    Write-Host "== $target already exists - pulling latest instead of cloning ==" -ForegroundColor Cyan
    Push-Location $target
    git pull
    Pop-Location
} else {
    Write-Host "== Cloning into $target ==" -ForegroundColor Cyan
    git clone $repoUrl $target
}

Set-Location $target
Write-Host "== Running install.ps1 ==" -ForegroundColor Cyan
& (Join-Path $target "install.ps1")
