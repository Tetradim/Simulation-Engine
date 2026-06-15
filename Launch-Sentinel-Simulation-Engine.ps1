# Sentinel Simulation Engine Launcher

param(
    [int]$Port = 9200,
    [switch]$NoBrowser,
    [switch]$InstallDeps,
    [switch]$Rebuild,
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }
$DesktopPath = [Environment]::GetFolderPath("Desktop")
if (-not $DesktopPath) { $DesktopPath = Join-Path $HOME "Desktop" }
$LogFile = Join-Path $DesktopPath "Sentinel-Simulation-Engine.log"
$ServerProcess = $null
$WatchdogProcess = $null
$WatchdogStopFile = $null
$WatchdogScriptFile = $null
$BrowserProcess = $null
$BrowserProfileDir = $null
$BrowserProcessIds = @()
$BrowserWindowProcessIds = @()
$BrowserStartedAt = $null
$BrowserMonitorDisabled = $false

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "[$Level] $Message" -ForegroundColor $color
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    Add-Content -Path $LogFile -Value "$timestamp [$Level] $Message" -Encoding UTF8
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $client = New-Object Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(750, $false)
        if ($connected) { $client.EndConnect($async) }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Join-ProcessArguments {
    param([string[]]$Arguments)

    return (($Arguments | ForEach-Object {
        $arg = $_
        if ([string]::IsNullOrEmpty($arg)) {
            '""'
        } elseif ($arg -match '[\s"]') {
            $escaped = $arg.Replace('"', '\"')
            '"' + $escaped + '"'
        } else {
            $arg
        }
    }) -join " ")
}

function Start-DedicatedBrowserWindow {
    param([string]$Url)

    $browserExe = Find-BrowserExecutable
    if ($browserExe) {
        Write-Status "Opening dedicated browser window"
        $script:BrowserProfileDir = Join-Path ([System.IO.Path]::GetTempPath()) "SentinelSimulationEngine-Browser-$PID"
        $script:BrowserStartedAt = Get-Date
        New-Item -ItemType Directory -Path $script:BrowserProfileDir -Force | Out-Null
        $browserArgs = Join-ProcessArguments -Arguments @(
            "--new-window",
            "--app=$Url",
            "--user-data-dir=$script:BrowserProfileDir",
            "--no-first-run",
            "--disable-background-mode"
        )
        $process = Start-Process -FilePath $browserExe -ArgumentList $browserArgs -PassThru
        Wait-BrowserProfileProcesses -Seconds 10 | Out-Null
        Wait-BrowserWindowProcesses -Seconds 10 | Out-Null
        return $process
    }

    Write-Status "Opening default browser without dedicated profile" "WARN"
    Start-Process $Url | Out-Null
    return $null
}

function Get-BrowserProfileProcesses {
    if (-not $BrowserProfileDir) { return @() }
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($BrowserProfileDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } |
            ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
    } catch {
        return @()
    }
}

function Get-BrowserWindowProcesses {
    return @(Get-BrowserProfileProcesses | Where-Object { $_.MainWindowHandle -and $_.MainWindowHandle -ne 0 })
}

function Update-BrowserProcessIds {
    $profileProcesses = @(Get-BrowserProfileProcesses)
    if ($profileProcesses.Count -gt 0) {
        $script:BrowserProcessIds = @($profileProcesses | Select-Object -ExpandProperty Id)
    }
    $windowProcesses = @($profileProcesses | Where-Object { $_.MainWindowHandle -and $_.MainWindowHandle -ne 0 })
    if ($windowProcesses.Count -gt 0) {
        $script:BrowserWindowProcessIds = @($windowProcesses | Select-Object -ExpandProperty Id)
    }
    return $profileProcesses
}

function Wait-BrowserProfileProcesses {
    param([int]$Seconds = 10)

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $profileProcesses = @(Update-BrowserProcessIds)
        if ($profileProcesses.Count -gt 0) { return $profileProcesses }
        Start-Sleep -Milliseconds 250
    }
    return @(Update-BrowserProcessIds)
}

function Wait-BrowserWindowProcesses {
    param([int]$Seconds = 10)

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        Update-BrowserProcessIds | Out-Null
        $windowProcesses = @(Get-BrowserWindowProcesses)
        if ($windowProcesses.Count -gt 0) {
            $script:BrowserWindowProcessIds = @($windowProcesses | Select-Object -ExpandProperty Id)
            return $windowProcesses
        }
        Start-Sleep -Milliseconds 250
    }
    Update-BrowserProcessIds | Out-Null
    return @(Get-BrowserWindowProcesses)
}

function Test-BrowserWindowClosed {
    if ($BrowserMonitorDisabled) { return $false }
    if (-not $BrowserProcess -and -not $BrowserProfileDir -and $BrowserProcessIds.Count -eq 0 -and $BrowserWindowProcessIds.Count -eq 0) { return $false }

    $profileProcesses = @(Update-BrowserProcessIds)
    $windowProcesses = @(Get-BrowserWindowProcesses)
    if ($windowProcesses.Count -gt 0) {
        $script:BrowserWindowProcessIds = @($windowProcesses | Select-Object -ExpandProperty Id)
        return $false
    }

    $knownWindowProcesses = @($BrowserWindowProcessIds | ForEach-Object {
        $process = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -and $process.MainWindowHandle -ne 0) { $process }
    })
    if ($knownWindowProcesses.Count -gt 0) { return $false }
    if ($BrowserWindowProcessIds.Count -gt 0) { return $true }

    $knownProcesses = @($BrowserProcessIds | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($knownProcesses.Count -gt 0) { return $false }
    if ($BrowserProcessIds.Count -gt 0) { return $true }

    if ($BrowserProfileDir -and $BrowserStartedAt) {
        $elapsed = ((Get-Date) - $BrowserStartedAt).TotalSeconds
        if ($elapsed -lt 15 -and $profileProcesses.Count -gt 0) { return $false }
        if ($profileProcesses.Count -gt 0) { return $true }
    }

    if ($BrowserProcess -and $BrowserProcess.HasExited) {
        return $true
    }
    return $false
}

function Wait-SimulationEngine {
    param([int]$Port, [int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -Method Get -TimeoutSec 3
            if ($health.service -eq "sentinel-simulation-engine") { return $true }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Find-CommandPath {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Find-BrowserExecutable {
    $candidates = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    foreach ($name in @("msedge.exe", "chrome.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Stop-PortOwnerProcess {
    param([int]$Port)
    $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 })
    foreach ($owner in $owners) {
        Write-Status "Replacing existing process $owner on port $Port" "WARN"
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
}

function Start-LauncherWatchdog {
    param([int]$ServerProcessId, [string]$BrowserProfileDir)

    $watchdogName = "SentinelSimulationEngine-Watchdog-$PID"
    $script:WatchdogStopFile = Join-Path ([System.IO.Path]::GetTempPath()) "$watchdogName.stop"
    $script:WatchdogScriptFile = Join-Path ([System.IO.Path]::GetTempPath()) "$watchdogName.ps1"
    if (Test-Path $script:WatchdogStopFile) {
        Remove-Item -LiteralPath $script:WatchdogStopFile -Force -ErrorAction SilentlyContinue
    }

    $watchdogScript = @'
param(
    [int]$ParentProcessId,
    [int]$ServerProcessId,
    [string]$BrowserProfileDir,
    [string]$StopFile,
    [string]$LogFile
)

function Write-WatchdogLog {
    param([string]$Message)
    if (-not $LogFile) { return }
    try {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
        Add-Content -Path $LogFile -Value "$timestamp [WATCHDOG] $Message" -Encoding UTF8
    } catch {
    }
}

function Get-ProfileProcesses {
    if (-not $BrowserProfileDir) { return @() }
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($BrowserProfileDir, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 } |
            ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
    } catch {
        return @()
    }
}

function Stop-ProcessTreeById {
    param([int]$ProcessId)
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            Stop-ProcessTreeById -ProcessId $child.ProcessId
        }
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    } catch {
    }
}

try {
    while ($true) {
        if ($StopFile -and (Test-Path -LiteralPath $StopFile)) { exit 0 }
        $parent = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
        if (-not $parent) { break }
        Start-Sleep -Seconds 1
    }

    Write-WatchdogLog "Launcher process $ParentProcessId ended; closing browser and stopping server"
    $profileProcesses = @(Get-ProfileProcesses)
    foreach ($process in $profileProcesses) {
        try { $process.CloseMainWindow() | Out-Null } catch {}
    }
    Start-Sleep -Milliseconds 750
    foreach ($process in $profileProcesses) {
        Stop-ProcessTreeById -ProcessId $process.Id
    }

    Stop-ProcessTreeById -ProcessId $ServerProcessId

    if ($BrowserProfileDir -and (Test-Path -LiteralPath $BrowserProfileDir)) {
        Remove-Item -LiteralPath $BrowserProfileDir -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {
    Write-WatchdogLog $_.Exception.Message
}
'@

    Set-Content -Path $script:WatchdogScriptFile -Value $watchdogScript -Encoding UTF8
    $watchdogArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $script:WatchdogScriptFile,
        "-ParentProcessId", "$PID",
        "-ServerProcessId", "$ServerProcessId",
        "-BrowserProfileDir", "$BrowserProfileDir",
        "-StopFile", $script:WatchdogStopFile,
        "-LogFile", $LogFile
    )
    $script:WatchdogProcess = Start-Process -FilePath "powershell.exe" -ArgumentList (Join-ProcessArguments -Arguments $watchdogArgs) -WindowStyle Hidden -PassThru
}

function Stop-LauncherWatchdog {
    if ($script:WatchdogStopFile) {
        New-Item -ItemType File -Path $script:WatchdogStopFile -Force -ErrorAction SilentlyContinue | Out-Null
    }
    if ($script:WatchdogProcess -and -not $script:WatchdogProcess.HasExited) {
        try {
            $script:WatchdogProcess.WaitForExit(2000) | Out-Null
            if (-not $script:WatchdogProcess.HasExited) {
                Stop-Process -Id $script:WatchdogProcess.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {
        }
    }
    if ($script:WatchdogScriptFile -and (Test-Path $script:WatchdogScriptFile)) {
        Remove-Item -LiteralPath $script:WatchdogScriptFile -Force -ErrorAction SilentlyContinue
    }
    if ($script:WatchdogStopFile -and (Test-Path $script:WatchdogStopFile)) {
        Remove-Item -LiteralPath $script:WatchdogStopFile -Force -ErrorAction SilentlyContinue
    }
}

function Stop-BrowserWindow {
    $profileProcesses = @(Get-BrowserProfileProcesses)
    try {
        foreach ($current in $profileProcesses) {
            $current.CloseMainWindow() | Out-Null
        }
        Start-Sleep -Milliseconds 500
        foreach ($current in $profileProcesses) {
            $remaining = Get-Process -Id $current.Id -ErrorAction SilentlyContinue
            if ($remaining) {
                Stop-Process -Id $remaining.Id -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
    }
    if ($profileProcesses.Count -eq 0 -and $BrowserProcess) {
        try {
            $current = Get-Process -Id $BrowserProcess.Id -ErrorAction SilentlyContinue
            if ($current) {
                $current.CloseMainWindow() | Out-Null
                Start-Sleep -Milliseconds 500
                $current = Get-Process -Id $BrowserProcess.Id -ErrorAction SilentlyContinue
                if ($current) {
                    Stop-Process -Id $current.Id -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {
        }
    }
    if ($BrowserProfileDir -and (Test-Path $BrowserProfileDir)) {
        try { Remove-Item -LiteralPath $BrowserProfileDir -Recurse -Force -ErrorAction SilentlyContinue } catch {}
    }
}

if ($SmokeTest) {
    Write-Status "Running launcher smoke test"
    if (-not (Find-CommandPath -Names @("python.exe", "python"))) { throw "Python was not found." }
    if (-not (Find-CommandPath -Names @("npm.cmd", "npm.exe", "npm"))) { throw "npm was not found." }
    Write-Status "Launcher smoke test passed" "OK"
    exit 0
}

try {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Sentinel Simulation Engine" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Status "Project root: $ProjectRoot"
    Write-Status "Launcher log: $LogFile"

    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $systemPython = Find-CommandPath -Names @("python.exe", "python")
        if (-not $systemPython) { throw "Python 3.11+ was not found." }
        Write-Status "Creating Python virtual environment"
        & $systemPython -m venv (Join-Path $ProjectRoot ".venv")
    }

    $npm = Find-CommandPath -Names @("npm.cmd", "npm.exe", "npm")
    if (-not $npm) { throw "npm was not found. Install Node.js 20+." }

    if ($InstallDeps -or -not (Test-Path (Join-Path $ProjectRoot ".venv\Lib\site-packages\fastapi"))) {
        Write-Status "Installing Python dependencies"
        & $python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "Python dependency install failed." }
    }

    if ($InstallDeps -or -not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
        Write-Status "Installing frontend dependencies"
        & $npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
    }

    if ($Rebuild -or -not (Test-Path (Join-Path $ProjectRoot "dist\index.html"))) {
        Write-Status "Building control panel"
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw "frontend build failed." }
    }

    if (Test-PortOpen -Port $Port) {
        Stop-PortOwnerProcess -Port $Port
        Start-Sleep -Seconds 1
    }

    Write-Status "Starting Simulation Engine on port $Port"
    $ServerProcess = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "simulation_engine.main:app", "--host", "127.0.0.1", "--port", "$Port") -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden
    if (-not (Wait-SimulationEngine -Port $Port)) {
        throw "Simulation Engine did not become ready on port $Port."
    }

    $url = "http://127.0.0.1:$Port"
    if (-not $NoBrowser) {
        $BrowserProcess = Start-DedicatedBrowserWindow -Url $url
    }
    Start-LauncherWatchdog -ServerProcessId $ServerProcess.Id -BrowserProfileDir $BrowserProfileDir

    Write-Host ""
    Write-Host "Ready: $url" -ForegroundColor Green
    Write-Host "Press Ctrl+C or close this window to stop the engine." -ForegroundColor Gray
    Write-Host ""

    while ($true) {
        if ($ServerProcess.HasExited) {
            throw "Simulation Engine exited unexpectedly with code $($ServerProcess.ExitCode)."
        }
        if (Test-BrowserWindowClosed) {
            Write-Status "Browser window closed; shutting down Simulation Engine" "OK"
            break
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Status $_.Exception.Message "ERROR"
    exit 1
} finally {
    Stop-LauncherWatchdog
    Stop-BrowserWindow
    if ($ServerProcess -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
