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

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "$name not found on PATH. $hint"
        exit 1
    }
}

Write-Host "== Checking prerequisites ==" -ForegroundColor Cyan
Require-Command "python" "Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
Require-Command "git" "Install Git from https://git-scm.com/download/win and re-run."

$pyVersion = (python --version) 2>&1
Write-Host "Found $pyVersion"

Write-Host "== Cloning ComfyUI ==" -ForegroundColor Cyan
if (-not (Test-Path "ComfyUI")) {
    git clone https://github.com/comfyanonymous/ComfyUI.git
} else {
    Write-Host "ComfyUI already cloned, skipping"
}

Write-Host "== Creating virtual environment ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
} else {
    Write-Host ".venv already exists, skipping"
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvHfCli = Join-Path $root ".venv\Scripts\huggingface-cli.exe"

Write-Host "== Installing Python dependencies ==" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r ComfyUI\requirements.txt
& $venvPython -m pip install -r requirements.txt

Write-Host "== Preparing config ==" -ForegroundColor Cyan
if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
    Write-Host "Created config.json from template - edit remote_ui_base_url before running the pipeline." -ForegroundColor Yellow
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
    Write-Host "== Registering auto-start at logon ==" -ForegroundColor Cyan
    $taskName = "MiniMaxH3Pipeline"
    $bgScript = Join-Path $root "run-background.bat"
    $action = New-ScheduledTaskAction -Execute $bgScript -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -Hidden `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName `
        -Action $action -Trigger $trigger -Settings $settings `
        -Description "Polls remote UI for MiniMax H3 video generation jobs" | Out-Null
    Write-Host "Registered scheduled task '$taskName' - will auto-start at every future logon." -ForegroundColor Green

    Write-Host "== Starting it now ==" -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started. Remove auto-start: .\uninstall-autostart.ps1"
}

Write-Host "== Done ==" -ForegroundColor Green
Write-Host "No manual workflow export needed - setup_workflow.py handled it."
Write-Host "One thing to still edit: config.json's remote_ui_base_url - it's a placeholder until you point it at a real service."
Write-Host "Logs: $root\logs\pipeline.log"
