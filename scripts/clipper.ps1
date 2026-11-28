<#
.SYNOPSIS
  Start Podcast Clipper and open it in one step (what the desktop shortcut runs).

.DESCRIPTION
  - Already running on http://127.0.0.1:3000: opens a window.
  - Not running: builds the app if its code changed since the last build, starts it in the
    background (no console window; output in %LOCALAPPDATA%\ai-podcast-clipper\app.log), then
    opens a window.
  - Running an older build (after you pulled or merged new code): stops it, builds, starts it.
  The window is Chrome's or Edge's app mode (no tabs or address bar). Closing it leaves the
  server running, so the next open is instant; a video being processed carries on either way.

.PARAMETER NoOpen
  Start the server only, without a window (the sign-in shortcut uses this).
.PARAMETER Stop
  Stop the server. A video being processed carries on.
.PARAMETER Rebuild
  Build again even if nothing changed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\clipper.ps1
#>
param([switch]$NoOpen, [switch]$Stop, [switch]$Rebuild)

$ErrorActionPreference = 'Stop'
$Port = 3000
$Url = "http://127.0.0.1:$Port"
$Frontend = Join-Path (Split-Path -Parent $PSScriptRoot) 'ai-podcast-clipper-frontend'
$AppDir = Join-Path $env:LOCALAPPDATA 'ai-podcast-clipper'
$Log = Join-Path $AppDir 'app.log'
$BuildId = Join-Path $Frontend '.next\BUILD_ID'

function Test-AppUp {
    try {
        $response = Invoke-WebRequest -Uri "$Url/manifest.webmanifest" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-ServerProcess {
    $ids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($id in $ids) {
        $process = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($process) { $process }
    }
}

function Stop-App {
    $stopped = $false
    foreach ($process in Get-ServerProcess) {
        if ($process.ProcessName -eq 'node') {
            Stop-Process -Id $process.Id -Force
            Write-Host "Stopped the app (node, pid $($process.Id))."
            $stopped = $true
        } else {
            Write-Host "Port $Port is used by $($process.ProcessName) (pid $($process.Id)), not this app: left alone."
        }
    }
    if (-not $stopped) { Write-Host 'The app was not running.' }
    for ($i = 0; $i -lt 20 -and (Test-AppUp); $i++) { Start-Sleep -Milliseconds 250 }
}

function Test-BuildNeeded {
    if ($Rebuild -or -not (Test-Path $BuildId)) { return $true }
    $built = (Get-Item $BuildId).LastWriteTimeUtc
    $folders = @('src', 'public') | ForEach-Object { Join-Path $Frontend $_ } | Where-Object { Test-Path $_ }
    $newer = Get-ChildItem -Path $folders -Recurse -File |
        Where-Object { $_.LastWriteTimeUtc -gt $built } | Select-Object -First 1
    if ($newer) { return $true }
    foreach ($name in 'package.json', 'package-lock.json', 'next.config.js', 'tsconfig.json', 'postcss.config.js', '.env') {
        $file = Join-Path $Frontend $name
        if ((Test-Path $file) -and (Get-Item $file).LastWriteTimeUtc -gt $built) { return $true }
    }
    return $false
}

function Invoke-Npm([string[]]$NpmArgs) {
    Push-Location $Frontend
    try {
        & npm.cmd @NpmArgs
        if ($LASTEXITCODE -ne 0) { throw "npm $($NpmArgs -join ' ') failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

function Start-App {
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
        Write-Host 'Installing the app''s packages (first start only)...'
        Invoke-Npm @('install')
    }
    if (Test-BuildNeeded) {
        Write-Host 'Building the app (its code changed since the last build, about a minute)...'
        Invoke-Npm @('run', 'build')
    }
    Write-Host 'Starting Podcast Clipper...'
    Add-Content -Path $Log -Value "`r`n=== $(Get-Date -Format s) start ==="
    # cmd /c so the output can be appended to the log; Hidden: no console window.
    Start-Process -FilePath $env:ComSpec -ArgumentList "/c npm.cmd run start >> `"$Log`" 2>&1" `
        -WorkingDirectory $Frontend -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-AppUp)) {
        if ((Get-Date) -gt $deadline) { throw "The app did not start within 90 s. See $Log" }
        Start-Sleep -Milliseconds 500
    }
}

function Open-App {
    $browsers = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $browser = $browsers | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($browser) {
        Start-Process -FilePath $browser -ArgumentList "--app=$Url"
    } else {
        Start-Process $Url
    }
}

try {
    if ($Stop) {
        Stop-App
        return
    }
    if (Test-AppUp) {
        # A production server from an older build: replace it. (No BUILD_ID means `npm run dev`
        # is serving: leave it alone.)
        if ((Test-Path $BuildId) -and (Test-BuildNeeded)) {
            Write-Host 'The code changed since this build: restarting with a new build.'
            Stop-App
            Start-App
        }
    } else {
        Start-App
    }
    if (-not $NoOpen) { Open-App }
} catch {
    Write-Host "Podcast Clipper could not start: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Press Enter to close.'
    [void](Read-Host)
    exit 1
}
