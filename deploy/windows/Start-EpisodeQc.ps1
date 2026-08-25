param(
    [string]$ProjectRoot = "",
    [string]$WorkspaceRoot = "",
    [string]$PublicHost = $env:EPISODE_QC_PUBLIC_HOST,
    [int]$Port = 8765,
    [string]$FlowUrl = $env:EPISODE_QC_FLOW_URL,
    [string]$NasProbePath = $env:EPISODE_QC_NAS_PROBE_PATH,
    [string]$FlowNasRoot = $env:EPISODE_QC_FLOW_NAS_ROOT,
    [string]$NasMountRoot = $env:EPISODE_QC_NAS_MOUNT_ROOT,
    [ValidateRange(1, 3600)]
    [int]$RestartDelaySeconds = 10,
    [ValidateRange(1, 3600)]
    [int]$NasRetrySeconds = 30,
    [switch]$Standalone,
    [switch]$NoToken
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not $WorkspaceRoot) {
    $WorkspaceRoot = Join-Path $env:LOCALAPPDATA "Episode-QC\workspace"
}
if (-not $PublicHost) {
    throw "EPISODE_QC_PUBLIC_HOST is required for LAN deployment."
}

$episodeQc = Join-Path $ProjectRoot ".venv\Scripts\episode-qc.exe"
if (-not (Test-Path -LiteralPath $episodeQc -PathType Leaf)) {
    throw "Episode QC executable is missing: $episodeQc"
}
$logs = Join-Path $WorkspaceRoot "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$outputLog = Join-Path $logs "episode-qc.out.log"
$errorLog = Join-Path $logs "episode-qc.err.log"

function Write-ServiceError([string]$Message) {
    try {
        $timestamp = (Get-Date).ToString("o")
        Add-Content -LiteralPath $errorLog -Encoding UTF8 -Value "$timestamp $Message"
    }
    catch {
        # Logging must never terminate the service watchdog.
    }
}
if ($FlowUrl) {
    $env:EPISODE_QC_FLOW_URL = $FlowUrl
}
if ($NasProbePath) {
    $env:EPISODE_QC_NAS_PROBE_PATH = $NasProbePath
}
else {
    Remove-Item Env:EPISODE_QC_NAS_PROBE_PATH -ErrorAction SilentlyContinue
}
if ([bool]$FlowNasRoot -ne [bool]$NasMountRoot) {
    throw "EPISODE_QC_FLOW_NAS_ROOT and EPISODE_QC_NAS_MOUNT_ROOT must be configured together."
}
if ($FlowNasRoot) {
    $env:EPISODE_QC_FLOW_NAS_ROOT = $FlowNasRoot
    $env:EPISODE_QC_NAS_MOUNT_ROOT = $NasMountRoot
}
else {
    Remove-Item Env:EPISODE_QC_FLOW_NAS_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:EPISODE_QC_NAS_MOUNT_ROOT -ErrorAction SilentlyContinue
}
$env:PYTHONUNBUFFERED = "1"

$arguments = @(
    "web",
    "--host", "0.0.0.0",
    "--public-host", $PublicHost,
    "--port", [string]$Port,
    "--workspace-root", $WorkspaceRoot,
    "--no-browser"
)
if ($Standalone) { $arguments += "--standalone" }
if ($NoToken) { $arguments += "--no-token" }

Push-Location $ProjectRoot
try {
    while ($true) {
        try {
            $runId = [guid]::NewGuid().ToString("N")
            $runOutput = Join-Path $logs "episode-qc.$runId.out.tmp.log"
            $runError = Join-Path $logs "episode-qc.$runId.err.tmp.log"
            $argumentLine = ($arguments | ForEach-Object {
                if ($_ -match '[\s"]') {
                    '"' + ($_ -replace '"', '\"') + '"'
                }
                else {
                    $_
                }
            }) -join ' '
            $process = Start-Process `
                -FilePath $episodeQc `
                -ArgumentList $argumentLine `
                -RedirectStandardOutput $runOutput `
                -RedirectStandardError $runError `
                -NoNewWindow `
                -Wait `
                -PassThru
            $exitCode = $process.ExitCode
            foreach ($logPair in @(
                @($runOutput, $outputLog),
                @($runError, $errorLog)
            )) {
                if (Test-Path -LiteralPath $logPair[0]) {
                    Get-Content -LiteralPath $logPair[0] -Raw | `
                        Add-Content -LiteralPath $logPair[1] -Encoding UTF8
                    Remove-Item -LiteralPath $logPair[0] -Force
                }
            }
            Write-ServiceError "Episode QC exited with code $exitCode; restarting in $RestartDelaySeconds seconds."
        }
        catch {
            Write-ServiceError "Episode QC watchdog iteration failed: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
finally {
    Pop-Location
}
