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
