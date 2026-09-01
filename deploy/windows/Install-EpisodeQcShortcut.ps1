param(
    [string]$ProjectRoot = "D:\Episode-QC",
    [string]$WorkspaceRoot = "D:\Episode-QC-Workspace",
    [string]$TaskName = "Episode QC",
    [int]$Port = 8765,
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Episode QC.lnk")
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $ProjectRoot "deploy\windows\Open-EpisodeQc.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Episode QC launcher is missing: $launcher"
}

$powerShell = Join-Path $PSHOME "powershell.exe"
$arguments = @(
    "-NoLogo",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$launcher`"",
    "-TaskName", "`"$TaskName`"",
    "-WorkspaceRoot", "`"$WorkspaceRoot`"",
    "-Port", [string]$Port
) -join " "

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $powerShell
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.WindowStyle = 7
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,220"
$shortcut.Description = "Start and open Episode QC"
$shortcut.Save()

Write-Output "Episode QC shortcut installed: $ShortcutPath"
