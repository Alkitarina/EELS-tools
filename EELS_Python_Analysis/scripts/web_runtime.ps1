function Get-EelsHealth([int]$PortNumber) {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$PortNumber/health" -TimeoutSec 2
    }
    catch { return $null }
}

function Test-EelsHealth($Health, [string]$ProjectRoot) {
    return (
        $null -ne $Health -and $Health.status -eq "ok" -and
        $Health.application -eq "eels-python-analysis" -and
        $Health.launcher_protocol -eq 1 -and
        $Health.project_root -ieq $ProjectRoot -and
        $Health.pid -gt 0 -and $Health.instance_id
    )
}

function Test-EelsCompatibleHealth($Health, [string]$ProjectRoot, [string]$ExpectedSourceId = "") {
    return (
        (Test-EelsHealth $Health $ProjectRoot) -and
        $Health.result_format -eq "eels_peak_results_v1" -and
        $Health.bootstrap_engine -eq "cpu_parallel_v1" -and
        $Health.reference_maps_version -eq "reference_maps_v1" -and
        $Health.region_fit_version -eq "contrast_region_fit_v1" -and
        $Health.workflow_version -eq "eels_interface_workflow_v1" -and
        $Health.workflow_revision -eq "interface_y_stripes_v1" -and
        $Health.ui_preferences_version -eq "eels_ui_preferences_v1" -and
        $Health.strip_data_version -eq "independent_full_x_v1" -and
        $Health.empty_startup_version -eq "deferred_data_v1" -and
        (-not $ExpectedSourceId -or $Health.ui_source_id -eq $ExpectedSourceId)
    )
}

function Save-EelsStartupData([string]$StateFile, [string]$ProjectRoot, [string]$DataFile) {
    # Optional launch history only; never read to auto-load data at startup.
    try {
        if (-not (Test-Path -LiteralPath $DataFile -PathType Leaf)) { return }
        [IO.Directory]::CreateDirectory((Split-Path -Parent $StateFile)) | Out-Null
        $state = [ordered]@{
            format = "eels_startup_v1"
            project_root = $ProjectRoot
            data_file = $DataFile
            saved_utc = [DateTime]::UtcNow.ToString("o")
        } | ConvertTo-Json
        [IO.File]::WriteAllText($StateFile, $state, [Text.UTF8Encoding]::new($false))
    }
    catch { Write-Warning "The server is ready, but its startup file could not be saved: $StateFile" }
}

function Test-EelsPortInUse([int]$PortNumber) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync("127.0.0.1", $PortNumber)
        return $pending.Wait(300) -and $client.Connected
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Get-EelsListenerIds([int]$PortNumber) {
    $lines = & "$env:SystemRoot\System32\netstat.exe" -ano -p TCP
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect TCP listeners safely." }
    $ids = foreach ($line in $lines) {
        $parts = @($line.Trim() -split "\s+")
        if ($parts.Count -ge 5 -and $parts[0] -eq "TCP" -and $parts[3] -eq "LISTENING" -and
            $parts[1] -match ":(?<port>\d+)$" -and [int]$Matches.port -eq $PortNumber) {
            [int]$parts[4]
        }
    }
    return @($ids | Sort-Object -Unique)
}
