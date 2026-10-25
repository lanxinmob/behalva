param([switch]$Test)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$env:JAVA_HOME=Split-Path (Split-Path (Get-Command java).Source -Parent) -Parent
$env:ANDROID_HOME="$root\.tools\android-sdk"
$env:JAVA_TOOL_OPTIONS='-Xmx768m'
Set-Content -LiteralPath "$root\android\local.properties" -Value ('sdk.dir='+$env:ANDROID_HOME.Replace('\','/')) -Encoding utf8
$proxyArgs=@()
if($env:HTTPS_PROXY){
  $proxyUri=[uri]$env:HTTPS_PROXY
  if($proxyUri.UserInfo){throw 'Authenticated build proxy must be configured separately'}
  $proxyArgs=@("-Dhttps.proxyHost=$($proxyUri.Host)","-Dhttps.proxyPort=$($proxyUri.Port)","-Dhttp.proxyHost=$($proxyUri.Host)","-Dhttp.proxyPort=$($proxyUri.Port)")
}
$tasks=@('assembleDebug')
if($Test){
  & "$root\.venv\Scripts\python.exe" "$root\tools\prepare_android_test.py"
  if($LASTEXITCODE -ne 0){throw 'Android test runtime preparation failed'}
  $tasks+='testDebugUnitTest'
}
& "$root\.tools\gradle-8.9\bin\gradle.bat" -p "$root\android" --no-daemon --max-workers=1 @proxyArgs @tasks
if($LASTEXITCODE -ne 0){throw 'Android build failed'}
