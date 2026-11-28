<#
.SYNOPSIS
  Move the app's data (videos' clips, transcripts, stats, settings) out of the repo, and so
  out of OneDrive, into %LOCALAPPDATA%\ai-podcast-clipper\data. Run it once.

.DESCRIPTION
  Versions before this one kept their data in the repo's data folder. The app now uses
  %LOCALAPPDATA%\ai-podcast-clipper\data, but keeps reading the repo's folder until it is
  moved. This stops the app's server, refuses while a video is processing, then moves every
  file with robocopy (nothing is deleted before it is copied).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\move-data.ps1
#>
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Old = Join-Path $Repo 'data'
$New = Join-Path $env:LOCALAPPDATA 'ai-podcast-clipper\data'

if (-not (Test-Path $Old)) {
    Write-Host "Nothing to move: $Old does not exist."
    return
}
if (Test-Path (Join-Path $New 'jobs')) {
    throw "$New already holds jobs. Merge the two folders by hand, then delete $Old."
}
$runners = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like 'python*.exe' -and $_.CommandLine -like '*runner.py*' }
if ($runners) {
    throw 'A video is still processing. Let it finish (or cancel it in the app), then run this again.'
}

& (Join-Path $PSScriptRoot 'clipper.ps1') -Stop

New-Item -ItemType Directory -Force -Path $New | Out-Null
Write-Host "Moving $Old to $New ..."
robocopy $Old $New /E /MOVE /R:3 /W:2 /NFL /NDL /NP /NJH
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed (exit code $LASTEXITCODE). Nothing is lost: what was not moved is still in $Old."
}
Write-Host "Done: the data is in $New."

$envFile = Join-Path $Repo 'ai-podcast-clipper-frontend\.env'
if ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*DATA_DIR\s*=' -Quiet)) {
    Write-Host ''
    Write-Host 'ai-podcast-clipper-frontend\.env sets DATA_DIR, which overrides the new folder.' -ForegroundColor Yellow
    Write-Host "Delete that line (or set DATA_DIR=$New) before starting the app." -ForegroundColor Yellow
}
Write-Host 'Start the app again with the Podcast Clipper shortcut (or scripts\clipper.ps1).'
