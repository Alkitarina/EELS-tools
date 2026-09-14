[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DataDir,
    [string]$OutputDir,
    [string[]]$Select,
    [switch]$List,
    [switch]$ComputeMissingUncertainty,
    [int]$Resamples=500,
    [int]$BlockSize=2,
    [int]$Seed=20260728,
    [int]$Workers=0,
    [switch]$Resume,
    [switch]$Individual,
    [switch]$NoIndividual
)
$ErrorActionPreference='Stop'
$plotProjectRoot=Split-Path -Parent $PSScriptRoot
$plotPython=Join-Path $plotProjectRoot 'EELS_Python_Analysis\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $plotPython)) { throw "Project Python environment not found: $plotPython" }
$plotArguments=@('-B','-X','utf8',(Join-Path $PSScriptRoot 'plot_eels.py'),'--input-dir',$DataDir)
if ($OutputDir) { $plotArguments+=@('--output-dir',$OutputDir) }
if ($Select) { $plotArguments+=@('--select')+$Select }
if ($List) { $plotArguments+='--list' }
if ($ComputeMissingUncertainty) { $plotArguments+='--compute-missing-uncertainty' }
if ($Resume) { $plotArguments+='--resume' }
if ($Individual -and $NoIndividual) { throw 'Choose at most one of -Individual and -NoIndividual.' }
if ($Individual) { $plotArguments+='--individual' }
if ($NoIndividual) { $plotArguments+='--no-individual' }
$plotArguments+=@('--resamples',"$Resamples",'--block-size',"$BlockSize",'--seed',"$Seed",'--workers',"$Workers")
& $plotPython @plotArguments
if ($LASTEXITCODE -ne 0) { throw "Plotting failed (exit $LASTEXITCODE). See the preceding error." }
