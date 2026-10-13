[CmdletBinding()]
param(
    [string]$DataFile = "",
    [ValidateRange(1, 65535)][int]$Port = 8768,
    # Retained for old shortcuts only. Port scanning is intentionally disabled.
    [ValidateRange(1, 30)][int]$MaxPortScan = 1,
    [ValidateRange(3, 600)][int]$StartupTimeoutSeconds = 60,
    [string]$LogDir = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
. (Join-Path $projectRoot "scripts\web_runtime.ps1")
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$server = Join-Path $projectRoot "eels_web.py"
$explicitData = -not [string]::IsNullOrWhiteSpace($DataFile)
if ($explicitData) {
    if (-not [System.IO.Path]::IsPathRooted($DataFile)) {
        $DataFile = Join-Path $projectRoot $DataFile
    }
    $DataFile = [System.IO.Path]::GetFullPath($DataFile)
    if (-not (Test-Path -LiteralPath $DataFile -PathType Leaf)) {
        throw "Data file not found: $DataFile"
    }
    if ([System.IO.Path]::GetExtension($DataFile).ToLowerInvariant() -notin @(".h5", ".hdf5")) {
        throw "Startup DataFile must be HDF5. Import toolbox MAT files from the web page."
    }
}
if (-not $LogDir) { $LogDir = Join-Path $projectRoot "web_runs" }
elseif (-not [System.IO.Path]::IsPathRooted($LogDir)) { $LogDir = Join-Path $projectRoot $LogDir }
$LogDir = [System.IO.Path]::GetFullPath($LogDir)
$stateFile = Join-Path $LogDir "last_launch.json"
if ($PSBoundParameters.ContainsKey("MaxPortScan")) {
    Write-Warning "-MaxPortScan is ignored. EELS stays on the requested port (default 8768)."
}

function Read-CurrentSourceId {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Missing .venv. Create the project Python environment first."
    }
    $identity = & $python -X utf8 (Join-Path $projectRoot "eelsfit\runtime_identity.py")
    if ($LASTEXITCODE -ne 0 -or $identity -notmatch "^[a-f0-9]{64}$") {
        throw "Cannot verify the current UI source. No existing server was changed."
    }
    return [string]$identity
}

$mutex = [System.Threading.Mutex]::new($false, "Local\EELSPythonAnalysisWebStart")
$locked = $false
$ready = $false
$process = $null
$health = $null
$stdoutLog = $null
$stderrLog = $null
try {
    try { $locked = $mutex.WaitOne(15000) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw "Another EELS launcher is still starting. Retry shortly." }

    $selectedPort = $Port
    while (-not $ready) {
        # The public UI never silently moves to a different port.
        if (Test-EelsPortInUse $Port) {
            $health = Get-EelsHealth $Port
            if (-not (Test-EelsHealth $health $projectRoot)) {
                throw "Port $Port is occupied by another application. Release that port and retry; no alternate port was started and nothing was stopped."
            }
            $expectedSourceId = Read-CurrentSourceId
            if (-not (Test-EelsCompatibleHealth $health $projectRoot $expectedSourceId)) {
                throw "Port $Port has an older EELS backend. Preserve/export its results, then run .\stop_web.ps1 -Port $Port and .\start_web.ps1 -Port $Port. The latest UI stays on $Port; the existing session was left running."
            }
            if ($explicitData -and $health.source -ine $DataFile) {
                throw "Port $Port already serves different data. Import the requested file in that UI, or export results and stop it before restarting on $Port. The existing data and results were preserved."
            }
            $ready = $true
            Write-Output "[start_web] Reusing existing EELS server; current data and results are preserved."
        }
        if ($ready) { break }
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "Missing .venv. Run: py -3.12 -m venv .venv; then .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        }
        # A fresh launch is empty unless the caller explicitly supplies -DataFile.
        # Never consult startup history, a legacy H5, or a file picker here.
        $expectedSourceId = Read-CurrentSourceId
        [System.IO.Directory]::CreateDirectory($logDir) | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
        $stdoutLog = Join-Path $logDir "web_server.$stamp.stdout.log"
        $stderrLog = Join-Path $logDir "web_server.$stamp.stderr.log"
        $arguments = "-X utf8 -u `"$server`" --port $selectedPort"
        if ($explicitData) { $arguments += " `"$DataFile`"" }
        $oldEncoding = $env:PYTHONIOENCODING
        try {
            $env:PYTHONIOENCODING = "utf-8"
            $process = Start-Process -FilePath $python -ArgumentList $arguments `
                -WorkingDirectory $projectRoot -WindowStyle Hidden `
                -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
        }
        finally { $env:PYTHONIOENCODING = $oldEncoding }
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        while ($watch.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds) {
            if ($process.HasExited) { break }
            $health = Get-EelsHealth $selectedPort
            $dataMatches = if ($explicitData) { $health.source -ieq $DataFile } else { $null -eq $health.source }
            if ((Test-EelsCompatibleHealth $health $projectRoot $expectedSourceId) -and $dataMatches) {
                $ready = $true
                break
            }
            Start-Sleep -Milliseconds 250
        }
        if ($ready) { break }
        $details = if (Test-Path -LiteralPath $stderrLog) {
            Get-Content -LiteralPath $stderrLog -Encoding UTF8 -Tail 30 | Out-String
        } else { "" }
        throw "EELS startup failed or timed out. Log: $stderrLog`n$details"
    }
    if (-not $ready) { throw "EELS could not start on its fixed port $Port." }
    if ($health.source) { Save-EelsStartupData $stateFile $projectRoot $health.source }
    $url = "http://127.0.0.1:$selectedPort"
    Write-Output "[start_web] Ready: $url"
    if ($health.source) { Write-Output "[start_web] Data: $($health.source)" }
    else { Write-Output "[start_web] No data loaded. Import MAT/H5, EELS-ZEST ZIP or workflow JSON in the browser." }
    Write-Output "[start_web] Server PID: $($health.pid)"
    if ($stdoutLog) { Write-Output "[start_web] Logs: $stdoutLog, $stderrLog" }
    Write-Output "[start_web] The server stays running after this terminal closes."
    Write-Output "[start_web] Stop: .\stop_web.ps1 -Port $selectedPort"
    Write-Output "EELS_WEB_URL=$url"
    Write-Output "EELS_WEB_PID=$($health.pid)"
    if (-not $NoBrowser) { Start-Process $url | Out-Null }
}
finally {
    # Kill only the process tree created by this failed launch, never an existing server.
    if (-not $ready -and $process -and -not $process.HasExited) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
    }
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
