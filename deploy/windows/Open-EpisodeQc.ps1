param(
    [string]$TaskName = "Episode QC",
    [string]$WorkspaceRoot = "D:\Episode-QC-Workspace",
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 60,
    [switch]$HealthCheckOnly
)

$ErrorActionPreference = "Stop"
$tokenPath = Join-Path $WorkspaceRoot ".web-token"
$healthUri = "http://127.0.0.1:$Port/api/health"

function Get-WebToken {
    if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
        return ""
    }
    return (Get-Content -LiteralPath $tokenPath -Raw).Trim()
}

function Test-EpisodeQcHealth {
    $token = Get-WebToken
    if (-not $token) {
        return $false
    }
    try {
        $response = Invoke-RestMethod `
            -UseBasicParsing `
            -Headers @{ "X-Episode-QC-Token" = $token } `
            -Uri $healthUri `
            -TimeoutSec 3
        return $response.ok -eq $true
    }
    catch {
        return $false
    }
}

function Show-LaunchError([string]$Message) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            $Message,
            "Episode QC",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    catch {
        Write-Error $Message
    }
}

try {
    if (-not (Test-EpisodeQcHealth)) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        if ($task.State -ne "Running") {
            Start-ScheduledTask -TaskName $TaskName
        }

        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        do {
            Start-Sleep -Milliseconds 500
            if (Test-EpisodeQcHealth) {
                break
            }
        } while ((Get-Date) -lt $deadline)
    }

    if (-not (Test-EpisodeQcHealth)) {
        throw "Episode QC did not become ready within $StartupTimeoutSeconds seconds. Contact an administrator to check the scheduled task and logs."
    }

    if ($HealthCheckOnly) {
        Write-Output "Episode QC is ready."
        exit 0
    }

    $token = Get-WebToken
    $encodedToken = [Uri]::EscapeDataString($token)
    Start-Process "http://127.0.0.1:$Port/?token=$encodedToken"
}
catch {
    Show-LaunchError $_.Exception.Message
    exit 1
}
