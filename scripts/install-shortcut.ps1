<#
.SYNOPSIS
  Add a "Podcast Clipper" shortcut to the desktop and the Start menu (run it once).

.DESCRIPTION
  The shortcut runs scripts\clipper.ps1: it starts the app if needed and opens it in its own
  window. Pin it to the taskbar from the Start menu if you like.

.PARAMETER StartAtSignIn
  Also start the app's server when you sign in to Windows (no window), so the shortcut opens
  instantly.
.PARAMETER Remove
  Remove the shortcuts this script made.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-shortcut.ps1 -StartAtSignIn
#>
param([switch]$StartAtSignIn, [switch]$Remove)

$ErrorActionPreference = 'Stop'
$Launcher = Join-Path $PSScriptRoot 'clipper.ps1'
$Icon = Join-Path $PSScriptRoot 'clipper.ico'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Name = 'Podcast Clipper.lnk'
$Places = @{
    Desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) $Name
    StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) $Name
    SignIn = Join-Path ([Environment]::GetFolderPath('Startup')) $Name
}

if ($Remove) {
    foreach ($path in $Places.Values) {
        if (Test-Path $path) {
            Remove-Item $path
            Write-Host "Removed $path"
        }
    }
    return
}

$shell = New-Object -ComObject WScript.Shell

function New-Shortcut([string]$Path, [string]$Extra) {
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $PowerShell
    $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`" $Extra".Trim()
    $link.WorkingDirectory = $PSScriptRoot
    $link.IconLocation = "$Icon,0"
    $link.WindowStyle = 7          # minimized: the console only shows if a build needs a minute
    $link.Description = 'Podcast Clipper: paste a YouTube link, get clips'
    $link.Save()
    Write-Host "Created $Path"
}

New-Shortcut $Places.Desktop ''
New-Shortcut $Places.StartMenu ''
if ($StartAtSignIn) {
    New-Shortcut $Places.SignIn '-NoOpen'
} elseif (Test-Path $Places.SignIn) {
    Remove-Item $Places.SignIn
    Write-Host "Removed $($Places.SignIn) (start at sign-in is off)"
}
