param(
    [string]$Ndk = $env:ANDROID_NDK_HOME,
    [string]$Go = 'go'
)
$ErrorActionPreference = 'Stop'
if (-not $Ndk -or -not (Test-Path -LiteralPath (Join-Path $Ndk 'source.properties'))) {
    throw 'Set ANDROID_NDK_HOME or pass -Ndk with Android NDK r29.'
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $Ndk 'toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'This helper currently requires the Windows NDK toolchain.' }
$output = Join-Path $projectRoot 'android/app/src/main/jniLibs/arm64-v8a/libassistant_tailnet.so'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
$names = @('GOOS','GOARCH','CGO_ENABLED','CC','CGO_CFLAGS','CGO_LDFLAGS')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name) }
Push-Location (Join-Path $projectRoot 'android/tailnet')
try {
    $env:GOOS = 'android'; $env:GOARCH = 'arm64'; $env:CGO_ENABLED = '1'
    $env:CC = '"' + $compiler + '"'
    $env:CGO_CFLAGS = '--target=aarch64-linux-android28'
    $env:CGO_LDFLAGS = '--target=aarch64-linux-android28 -Wl,-z,max-page-size=16384'
    & $Go build -p 2 -mod=readonly -trimpath -tags ts_omit_logtail -buildmode=c-shared -ldflags '-s -w' -o $output ./cmd/jni
    if ($LASTEXITCODE -ne 0) { throw 'Embedded Tailscale build failed.' }
    $sourceFiles = @(Get-ChildItem -File -Recurse (Join-Path $projectRoot 'android/tailnet') | Where-Object { $_.Extension -in @('.go','.c','.mod','.sum') })
    $sourceFiles += Get-Item -LiteralPath $PSCommandPath
    $sources = @{}
    foreach ($file in $sourceFiles) {
        $relative = [IO.Path]::GetRelativePath($projectRoot,$file.FullName).Replace('\','/')
        $sources[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
    }
    $manifest = @{ sources=$sources; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash; tailscale='v1.104.1'; tags='ts_omit_logtail'; abi='arm64-v8a' }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $output) 'provenance.json') -Encoding utf8NoBOM
    Get-FileHash -Algorithm SHA256 -LiteralPath $output
} finally {
    Pop-Location
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$previous[$name]) }
}
