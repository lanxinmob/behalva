param(
    [Parameter(Mandatory=$true)][string]$Release,
    [string]$Distro='Ubuntu-22.04',
    [string]$Service='personal-assistant',
    [string]$BrowserData,
    [int]$Port=8770
)
$ErrorActionPreference='Stop'
if($Release -notmatch '^/[A-Za-z0-9_./-]+$' -or $Distro -notmatch '^[A-Za-z0-9_.-]+$' -or $Service -notmatch '^[a-z][a-z0-9-]{0,50}$'){
    throw 'Invalid deployment arguments'
}
if($Port -lt 1 -or $Port -gt 65535){throw 'Invalid port'}
$script=Join-Path $PSScriptRoot 'start-wsl.ps1'
if($script.Contains('"') -or $BrowserData.Contains('"')){throw 'Invalid Windows path'}
if($BrowserData){$BrowserData=(Resolve-Path -LiteralPath $BrowserData).Path}
$taskName='Personal Assistant WSL2 - '+$Service
if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){throw 'Autostart task already exists; inspect it before replacing.'}
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$script+'" -Release '+$Release+' -Distro '+$Distro+' -Service '+$Service+' -Port '+$Port
if($BrowserData){$arguments+=' -BrowserData "'+$BrowserData+'"'}
$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Write-Output "Registered $taskName for Windows sign-in; not started."
