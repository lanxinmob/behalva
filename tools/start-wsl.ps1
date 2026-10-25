param(
    [Parameter(Mandatory=$true)][string]$Release,
    [string]$Distro='Ubuntu-22.04',
    [string]$Service='personal-assistant',
    [string]$BrowserData,
    [int]$Port=8770
)
$ErrorActionPreference='Stop'
if($Release -notmatch '^/[A-Za-z0-9_./-]+$' -or $Distro -notmatch '^[A-Za-z0-9_.-]+$' -or $Service -notmatch '^[a-z][a-z0-9-]{0,50}$'){
    throw 'Use simple Linux release, distro and service names.'
}
if($Port -lt 1 -or $Port -gt 65535){throw 'Invalid port'}
$guard=New-Object System.Threading.Mutex($false,('Local\PersonalAssistantWSL-'+$Service))
$owned=$false
$bridge=$null
$keeper=$null
$bridgeArgs=$null
$bridgePython=$null
$owner=[guid]::NewGuid().ToString('N')
try {
    try {$owned=$guard.WaitOne(0)} catch [System.Threading.AbandonedMutexException] {$owned=$true}
    if(-not $owned){throw 'This WSL workspace launcher is already running.'}
    if($BrowserData){
        $dataPath=(Resolve-Path -LiteralPath $BrowserData).Path
        if($dataPath.Contains('"')){throw 'Invalid browser workspace path'}
        $root=Split-Path $PSScriptRoot -Parent
        $bridgePython=Join-Path $root '.venv\Scripts\python.exe'
        if(-not (Test-Path -LiteralPath $bridgePython -PathType Leaf)){throw 'Windows browser Python is missing.'}
        foreach($file in @('worker.token','server.pem')){if(-not (Test-Path -LiteralPath (Join-Path $dataPath $file) -PathType Leaf)){throw "Missing browser file: $file"}}
        $bridgeArgs=@(
            ('"'+(Join-Path $root 'connectors\browser_worker.py')+'"'),'--data',('"'+$dataPath+'"'),
            '--url',"https://127.0.0.1:$Port",'--ca',('"'+(Join-Path $dataPath 'server.pem')+'"')
        )
        $bridge=Start-Process -FilePath $bridgePython -ArgumentList $bridgeArgs -WindowStyle Hidden -PassThru
    }
    $keeper=Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d',$Distro,'--exec',"$Release/.venv/bin/python","$Release/tools/wsl_service.py",'hold','--name',$Service,'--owner',$owner) -WindowStyle Hidden -PassThru
    while(-not $keeper.WaitForExit(3000)){
        if($bridgeArgs -and (-not $bridge -or $bridge.HasExited)){
            # A fresh browser session invalidates old pending UI commands; no action is replayed.
            $bridge=Start-Process -FilePath $bridgePython -ArgumentList $bridgeArgs -WindowStyle Hidden -PassThru
        }
    }
    if($keeper.ExitCode -ne 0){throw 'WSL service stopped; inspect its journal and workspace logs.'}
} finally {
    if($keeper -and -not $keeper.HasExited){
        & wsl.exe -d $Distro --exec "$Release/.venv/bin/python" "$Release/tools/wsl_service.py" stop-owned --name $Service --owner $owner
        if(-not $keeper.WaitForExit(30000)){Stop-Process -Id $keeper.Id -Force}
    }
    # Stop the adapter process tree too: Windows venv launchers may spawn a child.
    if($bridge -and -not $bridge.HasExited){& taskkill.exe /PID $bridge.Id /T /F | Out-Null}
    if($owned){$guard.ReleaseMutex()}
    $guard.Dispose()
}
