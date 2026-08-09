<#
    Removes the scheduled task created by autostart.ps1.
#>

$ErrorActionPreference = "Stop"
$taskName = "MiniMaxH3Pipeline"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "No scheduled task named '$taskName' found - nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Removed scheduled task '$taskName'." -ForegroundColor Green
