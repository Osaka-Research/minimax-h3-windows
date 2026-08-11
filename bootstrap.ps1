<#
    One-command installer entry point - meant to be run via:
      irm https://raw.githubusercontent.com/Osaka-Research/minimax-h3-windows/main/bootstrap.ps1 | iex
    Picks an install location automatically (see Select-InstallDrive below)
    and runs install.ps1 there. Deliberately assumes nothing about the host
    beyond PowerShell itself - doesn't even require git (downloads a zip
    instead if git isn't present yet; install.ps1 installs git properly
    afterward, since it needs it anyway to clone ComfyUI). For anything
    beyond the default full install (e.g. -SkipAutostart), clone the repo
    yourself and run install.ps1 directly instead.
#>

$ErrorActionPreference = "Stop"

# Fresh Windows installs typically default to a PowerShell execution policy
# that blocks running .ps1 FILES (this script itself is fine - it's piped
# into iex, not run as a file - but the install.ps1 file we invoke below
# would otherwise be blocked). Process scope, no admin needed.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$repoUrl = "https://github.com/Osaka-Research/minimax-h3-windows.git"
$zipUrl = "https://github.com/Osaka-Research/minimax-h3-windows/archive/refs/heads/main.zip"
$folderName = "minimax-h3-windows"
$minFreeGB = 80  # matches install.ps1's own free-space warning threshold

# A previous run may have landed on a different drive than the default (see
# Select-InstallDrive) - check for that before picking a location fresh, so
# re-running this command always finds and updates the same install rather
# than starting a second one somewhere else.
function Find-ExistingInstall {
    $candidates = @(Join-Path $env:USERPROFILE $folderName)
    $candidates += Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path "$($_.Name):\" $folderName }
    foreach ($c in ($candidates | Select-Object -Unique)) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

# Prefers the default (user profile) drive - keeps behavior predictable -
# but if it doesn't have room for the ~60-80GB this needs, automatically
# picks whichever fixed drive has the most free space instead of failing
# partway through a multi-GB download.
function Select-InstallDrive {
    $defaultDrive = $env:USERPROFILE.Substring(0, 1)
    $drives = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
        Where-Object { $_.Free } | Sort-Object -Property Free -Descending
    $defaultInfo = $drives | Where-Object { $_.Name -eq $defaultDrive }
    if ($defaultInfo -and ($defaultInfo.Free / 1GB) -ge $minFreeGB) {
        return $defaultDrive
    }
    $best = $drives | Select-Object -First 1
    if ($best -and $best.Name -ne $defaultDrive -and ($best.Free / 1GB) -ge $minFreeGB) {
        Write-Host "== ${defaultDrive}: doesn't have ${minFreeGB}GB free - using $($best.Name): instead (more free space) ==" -ForegroundColor Yellow
        return $best.Name
    }
    # Nothing qualifies - fall back to the default; install.ps1's own
    # free-space check will warn about it rather than fail silently here.
    return $defaultDrive
}

$target = Find-ExistingInstall
if (-not $target) {
    $drive = Select-InstallDrive
    $target = if ($drive -eq $env:USERPROFILE.Substring(0, 1)) {
        Join-Path $env:USERPROFILE $folderName
    } else {
        Join-Path "$($drive):\" $folderName
    }
}

if (Test-Path (Join-Path $target ".git")) {
    Write-Host "== $target already exists - pulling latest instead of cloning ==" -ForegroundColor Cyan
    Push-Location $target
    git pull
    Pop-Location
} elseif ((Test-Path $target) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    # Left over from an earlier run that had to fall back to a zip snapshot
    # (see the final branch below) because git wasn't installed yet. Now
    # that it is, convert this folder into a real git checkout in place
    # instead of just telling the user to delete and re-fetch by hand -
    # `git checkout -f` only touches tracked files (install.ps1, *.py,
    # README, etc.), so any already-downloaded ComfyUI/ or models/ (both
    # gitignored, never tracked) are left alone.
    Write-Host "== $target exists but predates git - converting it to a git checkout in place (keeps any already-downloaded ComfyUI/models) ==" -ForegroundColor Cyan
    Push-Location $target
    git init -q
    git remote add origin $repoUrl 2>$null
    git fetch origin main -q
    git checkout -f -B main origin/main
    git branch --set-upstream-to=origin/main main | Out-Null
    Pop-Location
} elseif (Test-Path $target) {
    # $target exists but git still isn't available (e.g. an earlier run got
    # a zip snapshot but never got as far as install.ps1 installing git).
    # Can't safely clone or convert in place without git, and re-fetching
    # the zip would collide with this existing folder - leave it alone.
    Write-Host "== $target already exists and git still isn't available - leaving it as-is ==" -ForegroundColor Cyan
    Write-Host "Delete that folder and re-run this command once git is installed if you want a fresh fetch."
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
