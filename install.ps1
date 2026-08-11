<#
    Sets up the MiniMax H3 pipeline on a Windows host, sized for a single
    gaming GPU (12GB+): clones ComfyUI, installs its deps, downloads the
    reduced-size quantized checkpoints (pruned INT8 diffusion model + NVFP4
    text encoder + VAEs, ~42.5GB total vs. ~118GB for full BF16), runs
    setup_workflow.py to auto-generate the API-format workflow (no manual
    browser export needed), then registers auto-start at logon. Re-run any
    time; each step is skipped or replaced idempotently.

    -SkipAutostart : do everything except registering the scheduled task.
#>

param(
    [switch]$SkipAutostart
)

# Register-ScheduledTask (below) needs an elevated process or it fails with
# "Access is denied". Elevate up front, before the ~42.5GB download/setup
# work, so a non-admin run only prompts once and never repeats that work -
# every step past this point is idempotent, so the elevated relaunch just
# skips whatever the non-elevated instance already finished.
if (-not $SkipAutostart) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        # $PSCommandPath is unreliable depending on how this script was
        # invoked (e.g. via the bootstrap.ps1 `irm | iex` one-liner's `&`
        # call) - $MyInvocation.MyCommand.Path is what the rest of this
        # script already relies on for its own path, further down.
        $scriptPath = $MyInvocation.MyCommand.Path
        if (-not $scriptPath) {
            Write-Error "Could not determine this script's own path to re-launch elevated. Run install.ps1 directly (e.g. '.\install.ps1') rather than through a pipe, then try again."
            exit 1
        }
        Write-Host "Re-launching elevated (required to register the auto-start scheduled task)..." -ForegroundColor Yellow
        # -NoExit: keeps the elevated window open after the script finishes
        # (or errors) instead of instantly closing, so output/errors are
        # actually readable instead of flashing shut.
        $psArgLine = '-NoExit -NoProfile -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
        if ($SkipAutostart) { $psArgLine += ' -SkipAutostart' }
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $psArgLine
        Write-Host "Continue in the new elevated window that just opened." -ForegroundColor Yellow
        exit
    }
}

$ErrorActionPreference = "Stop"

# Fresh Windows installs typically default to a PowerShell execution policy
# that blocks running .ps1 files at all. Process scope only affects this one
# session and needs no admin rights, so this is safe to always set.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Installers (winget, python.org, git-scm) write PATH to the registry, but an
# already-running PowerShell process keeps its own cached copy of PATH from
# when it started - re-reading from the registry picks up anything just
# installed without needing a new shell.
function Update-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
}

function Ensure-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) { return }
    Write-Host "winget not found - installing it first (needed to auto-install Python/Git)..." -ForegroundColor Cyan
    $bundle = Join-Path $env:TEMP "AppInstaller.msixbundle"
    Invoke-WebRequest -Uri "https://aka.ms/getwinget" -OutFile $bundle -UseBasicParsing
    Add-AppxPackage -Path $bundle
    Update-SessionPath
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Error "Could not install winget automatically (can happen on older/locked-down Windows builds). Install these manually, then re-run this script:`n  Python 3.10+: https://www.python.org/downloads/`n  Git: https://git-scm.com/download/win"
        exit 1
    }
}

# A fresh Windows install always has a fake python.exe/python3.exe on PATH
# under WindowsApps (the Microsoft Store "App Execution Alias" stub) even
# when no real Python is installed - it satisfies Get-Command but errors
# out ("Python was not found; run without arguments to install from the
# Microsoft Store...") the moment it's actually run. Must be treated as
# absent, not a real install - and since WindowsApps can also sit ahead of
# a just-installed real Python in PATH order, every later invocation in
# this script uses the resolved real path below rather than bare `python`.
function Get-RealCommandPath($cmdName) {
    $matches = Get-Command $cmdName -All -ErrorAction SilentlyContinue
    foreach ($m in $matches) {
        if ($m.Source -notlike "*\WindowsApps\*") { return $m.Source }
    }
    return $null
}

# Verified real winget package IDs (github.com/microsoft/winget-pkgs manifests):
# Git.Git, Python.Python.3.13.
function Ensure-Command($cmdName, $wingetId, $displayName) {
    $resolved = Get-RealCommandPath $cmdName
    if ($resolved) {
        Write-Host "$displayName found."
        return $resolved
    }
    Ensure-Winget
    Write-Host "Installing $displayName via winget ($wingetId)..." -ForegroundColor Cyan
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements
    Update-SessionPath
    $resolved = Get-RealCommandPath $cmdName
    if (-not $resolved) {
        Write-Error "$displayName installed but '$cmdName' isn't visible on PATH in this session yet. Close this PowerShell window, open a new one, and re-run this script - every step here is safe to re-run."
        exit 1
    }
    Write-Host "$displayName installed." -ForegroundColor Green
    return $resolved
}

Write-Host "== Checking prerequisites ==" -ForegroundColor Cyan
$pythonExe = Ensure-Command "python" "Python.Python.3.13" "Python"
Ensure-Command "git" "Git.Git" "Git" | Out-Null

$pyVersion = (& $pythonExe --version) 2>&1
Write-Host "Found $pyVersion"

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)
} else {
    Write-Warning "nvidia-smi not found - no NVIDIA GPU/driver detected. This project targets a single NVIDIA GPU via CUDA; AMD/Intel/no-GPU is not a supported path here (would need ROCm/DirectML, not built). Continuing anyway, but generation will very likely fail or run unusably slowly on CPU."
}

$freeGB = [math]::Round((Get-PSDrive -Name ($root.Substring(0,1))).Free / 1GB, 1)
if ($freeGB -lt 80) {
    Write-Warning "Only ${freeGB}GB free on this drive. ComfyUI + ~42.5GB of model weights need roughly 60-80GB total. Continuing anyway, but the download may fail partway through if you run out of space."
}

# ComfyUI's Windows install docs require this; not something pip installs.
if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "== Ensuring Visual C++ Redistributable is installed (required by ComfyUI on Windows) ==" -ForegroundColor Cyan
    winget install --id Microsoft.VCRedist.2015+.x64 --accept-source-agreements --accept-package-agreements -e 2>&1 | Out-Null
} else {
    Write-Warning "winget not found - couldn't auto-install the Visual C++ Redistributable. If ComfyUI fails to start with a DLL error, install it manually: https://aka.ms/vs/17/release/vc_redist.x64.exe"
}

Write-Host "== Cloning ComfyUI ==" -ForegroundColor Cyan
if (-not (Test-Path "ComfyUI")) {
    git clone https://github.com/comfyanonymous/ComfyUI.git
} else {
    Write-Host "ComfyUI already cloned, skipping"
}

Write-Host "== Creating virtual environment ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    & $pythonExe -m venv .venv
} else {
    Write-Host ".venv already exists, skipping"
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvHfCli = Join-Path $root ".venv\Scripts\huggingface-cli.exe"

Write-Host "== Installing Python dependencies ==" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip

# Must happen BEFORE requirements.txt: plain `pip install torch` on Windows
# pulls the CPU-only build from PyPI. Installing the CUDA build first means
# requirements.txt sees torch already satisfied and won't silently replace
# it with the CPU one. Matches ComfyUI's own documented Windows install order.
Write-Host "Installing PyTorch with CUDA support (this is the one most likely to silently end up CPU-only if skipped)..." -ForegroundColor Cyan
& $venvPython -m pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130

& $venvPython -m pip install -r ComfyUI\requirements.txt
& $venvPython -m pip install -r requirements.txt

$cudaCheck = & $venvPython -c "import torch; print(torch.cuda.is_available())" 2>&1
if ($cudaCheck -notmatch "True") {
    Write-Warning "torch.cuda.is_available() returned '$cudaCheck', not True - PyTorch installed without working CUDA. Generation will run on CPU (extremely slow/impractical) or fail. Check your NVIDIA driver version and see https://pytorch.org/get-started/locally/ for the matching CUDA build."
}

Write-Host "== Preparing config ==" -ForegroundColor Cyan
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
    Write-Host "Created config.json from template." -ForegroundColor Yellow
} else {
    Write-Host "config.json already exists, skipping"
}

Write-Host "== Downloading quantized model weights (single-GPU sized, ~42.5GB) ==" -ForegroundColor Cyan
$modelsRoot = Join-Path $root "ComfyUI\models"
$diffusionDir = Join-Path $modelsRoot "diffusion_models"
$textEncoderDir = Join-Path $modelsRoot "text_encoders"
$vaeDir = Join-Path $modelsRoot "vae"
New-Item -ItemType Directory -Force -Path $diffusionDir, $textEncoderDir, $vaeDir | Out-Null

function Download-IfMissing($repoId, $filename, $destDir) {
    $destPath = Join-Path $destDir $filename
    if (Test-Path $destPath) {
        Write-Host "$filename already present, skipping"
    } else {
        & $venvHfCli download $repoId $filename --local-dir $destDir
    }
}

$repoId = "Comfy-Org/MiniMax-H3"
Download-IfMissing $repoId "minimax_h3_fl2va_pruned_int8_convrot.safetensors" $diffusionDir
Download-IfMissing $repoId "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" $textEncoderDir
Download-IfMissing $repoId "minimax_h3_video_vae_fp16.safetensors" $vaeDir
Download-IfMissing $repoId "minimax_h3_audio_vae_fp32.safetensors" $vaeDir

Write-Host "== Setting up the generation workflow (automatic - downloads the official template, starts ComfyUI, converts it) ==" -ForegroundColor Cyan
& $venvPython setup_workflow.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "setup_workflow.py failed - see output above."
    exit 1
}

if (-not $SkipAutostart) {
    Write-Host "== Registering auto-start at logon + watchdog ==" -ForegroundColor Cyan
    $taskName = "MiniMaxH3Pipeline"
    $bgScript = Join-Path $root "run-background.bat"
    $action = New-ScheduledTaskAction -Execute $bgScript -WorkingDirectory $root

    # Two triggers: start at logon, and a recurring watchdog check every 15
    # minutes. IgnoreNew means the watchdog is a no-op while it's already
    # running - but if the process ever dies outside Task Scheduler's own
    # restart-on-failure window (see RestartCount/RestartInterval below,
    # which only covers the first few minutes after a crash), the watchdog
    # brings it back within 15 minutes instead of leaving it down until the
    # next logon/reboot.
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn
    $watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 15)

    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName `
        -Action $action -Trigger @($logonTrigger, $watchdogTrigger) -Settings $settings `
        -Description "Polls remote UI for MiniMax H3 video generation jobs" | Out-Null
    Write-Host "Registered scheduled task '$taskName' - auto-starts at logon and is checked/restarted every 15 min if down." -ForegroundColor Green

    Write-Host "== Starting it now ==" -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started. Remove auto-start: .\uninstall-autostart.ps1"
}

Write-Host "== Done ==" -ForegroundColor Green
Write-Host "No manual workflow export needed - setup_workflow.py handled it."
Write-Host "Logs: $root\logs\pipeline.log"
