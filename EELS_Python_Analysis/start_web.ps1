[CmdletBinding()]
param(
    [string]$DataFile = "",
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [ValidateRange(1, 30)][int]$MaxPortScan = 10,
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
if (-not $explicitData) { $DataFile = "2-15-result5.h5" }
if (-not [System.IO.Path]::IsPathRooted($DataFile)) {
    $DataFile = Join-Path $projectRoot $DataFile
}
$DataFile = [System.IO.Path]::GetFullPath($DataFile)
if ($explicitData -and -not (Test-Path -LiteralPath $DataFile -PathType Leaf)) {
    throw "Data file not found: $DataFile"
}
if ([System.IO.Path]::GetExtension($DataFile).ToLowerInvariant() -notin @(".h5", ".hdf5")) {
    throw "Startup DataFile must be HDF5. Import toolbox MAT files from the web page."
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

    $lastPort = [Math]::Min(65535, $Port + $MaxPortScan - 1)
    for ($selectedPort = $Port; $selectedPort -le $lastPort; $selectedPort++) {
        if (Test-EelsPortInUse $selectedPort) {
            $health = Get-EelsHealth $selectedPort
            if ((Test-EelsHealth $health $projectRoot) -and $health.result_format -eq "eels_peak_results_v1" -and $health.bootstrap_engine -eq "cpu_parallel_v1" -and $health.reference_maps_version -eq "reference_maps_v1" -and $health.region_fit_version -eq "contrast_region_fit_v1" -and
                (-not $explicitData -or $health.source -ieq $DataFile)) {
                $ready = $true
                Write-Output "[start_web] Reusing existing EELS server; current data and results are preserved."
                break
            }
            Write-Output "[start_web] Port $selectedPort is occupied or serves different data; trying the next port."
            continue
        }
        if (-not (Test-Path -LiteralPath $DataFile -PathType Leaf)) {
            throw "Data file not found: $DataFile. Pass -DataFile with an existing HDF5 file."
        }
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "Missing .venv. Run: py -3.12 -m venv .venv; then .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        }
        if (-not $LogDir) { $LogDir = Join-Path $projectRoot "web_runs" }
        elseif (-not [System.IO.Path]::IsPathRooted($LogDir)) { $LogDir = Join-Path $projectRoot $LogDir }
        [System.IO.Directory]::CreateDirectory($logDir) | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
        $stdoutLog = Join-Path $logDir "web_server.$stamp.stdout.log"
        $stderrLog = Join-Path $logDir "web_server.$stamp.stderr.log"
        $arguments = "-X utf8 -u `"$server`" `"$DataFile`" --port $selectedPort"
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
            if ((Test-EelsHealth $health $projectRoot) -and $health.result_format -eq "eels_peak_results_v1" -and $health.bootstrap_engine -eq "cpu_parallel_v1" -and $health.reference_maps_version -eq "reference_maps_v1" -and $health.region_fit_version -eq "contrast_region_fit_v1" -and $health.source -ieq $DataFile) {
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
    if (-not $ready) { throw "No usable port in $Port..$lastPort. Try -Port with another number." }
    $url = "http://127.0.0.1:$selectedPort"
    Write-Output "[start_web] Ready: $url"
    Write-Output "[start_web] Data: $($health.source)"
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
