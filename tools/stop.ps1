param([Parameter(Mandatory=$true)][string]$Data)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$runner=Join-Path $root 'tools\workspace.py'
$dataPath=(Resolve-Path -LiteralPath $Data).Path.TrimEnd('\')
# Match the exact invocation emitted by start.ps1, including its workspace.
$signature='(?i)"'+[regex]::Escape($runner)+'"\s+run\s+--data\s+"'+[regex]::Escape($dataPath)+'"(?=\s|$)'
$matches=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(?:w)?\.exe$' -and $_.CommandLine -match $signature })
# Windows virtualenv launchers may start a second Python with identical arguments.
$matchingIds=@($matches | ForEach-Object { $_.ProcessId })
$supervisors=@($matches | Where-Object { $_.ParentProcessId -notin $matchingIds })
if($supervisors.Count -ne 1){throw "Expected one supervisor for this workspace, found $($supervisors.Count). No process was stopped. Inspect the original startup and workspace logs."}
$targetId=$supervisors[0].ProcessId
& taskkill.exe /PID $targetId /T /F
if($LASTEXITCODE -ne 0){throw 'Stopping the workspace process tree did not complete. Inspect running processes before restarting.'}
Write-Output 'Workspace stopped. In-flight external actions must be checked before any retry.'
