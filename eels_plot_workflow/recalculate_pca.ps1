[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [Parameter(Mandatory=$true)][double]$MinProminencePercent,
    [switch]$Resume,
    [switch]$Plot
)
$ErrorActionPreference='Stop'
$recalcRoot=Split-Path -Parent $PSScriptRoot
$recalcPython=Join-Path $recalcRoot 'EELS_Python_Analysis\.venv\Scripts\python.exe'
$recalcArgs=@('-B','-X','utf8',(Join-Path $PSScriptRoot 'recalculate_pca.py'),
    '--input-dir',$DataDir,'--output-dir',$OutputDir,'--min-prominence-percent',
    $MinProminencePercent.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($Resume) { $recalcArgs+='--resume' }
& $recalcPython @recalcArgs
if ($LASTEXITCODE -ne 0) { throw "Recalculation failed (exit $LASTEXITCODE)." }
if ($Plot) {
    & (Join-Path $PSScriptRoot 'plot_eels.ps1') -DataDir $OutputDir
}
