[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8768,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "scripts\web_runtime.ps1")
$health = Get-EelsHealth $Port
if (-not (Test-EelsHealth $health $PSScriptRoot)) {
    throw "Port $Port is not a verified EELS server from this project. Nothing was stopped."
}
if ($health.busy -and -not $Force) {
    throw "An import or uncertainty job is active. Wait for it, or use -Force to discard it."
}
$listeners = @(Get-EelsListenerIds $Port)
if ($listeners.Count -ne 1 -or $listeners[0] -ne $health.pid) {
    throw "Health PID does not match the listener. Nothing was stopped."
}
$process = Get-Process -Id $health.pid -ErrorAction Stop
if ($process.ProcessName -notin @("python", "pythonw")) {
    throw "The listener is not Python. Nothing was stopped."
}
$confirmed = Get-EelsHealth $Port
if (-not (Test-EelsHealth $confirmed $PSScriptRoot) -or
    $confirmed.instance_id -ne $health.instance_id -or $confirmed.pid -ne $process.Id) {
    throw "The server identity changed. Nothing was stopped."
}
if ($confirmed.busy -and -not $Force) {
    throw "A job started during the check. Nothing was stopped."
}
Stop-Process -Id $process.Id -ErrorAction Stop
Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
Write-Output "[stop_web] Stopped EELS server on port $Port (PID $($process.Id))."
