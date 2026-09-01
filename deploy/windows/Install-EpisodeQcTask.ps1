param(
    [string]$TaskName = "Episode QC",
    [string]$StartScript = (Join-Path $PSScriptRoot "Start-EpisodeQc.ps1")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
    throw "QC start script is missing: $StartScript"
}

$credential = Get-Credential -Message "Dedicated Windows account that owns the QC workspace and SMB credentials"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$StartScript`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User $credential.UserName `
    -Password $credential.GetNetworkCredential().Password `
    -RunLevel Highest `
    -Force | Out-Null

Write-Output "Scheduled task installed: $TaskName"
Write-Output "Store SMB credentials under the same Windows account; no NAS password is written by this script."
