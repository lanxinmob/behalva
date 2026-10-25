param([string]$Data='D:\path\to\private-workspace',[string]$ListenHost='127.0.0.1',[int]$Port=8765,[switch]$Browser)
$ErrorActionPreference='Stop'
$Data=[System.IO.Path]::GetFullPath($Data).TrimEnd('\')
$root=Split-Path $PSScriptRoot -Parent
$python=Join-Path $root '.venv\Scripts\python.exe'
$runner=Join-Path $root 'tools\workspace.py'
$arguments=@("`"$runner`"",'run','--data',"`"$Data`"",'--host',$ListenHost,'--port',"$Port")
if($Browser){$arguments+='--browser'}
$process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
Write-Output "Supervisor process $($process.Id). Inspect $Data\logs for startup results."
