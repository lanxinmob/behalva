param([string]$Compiler='g++',[string]$OutputName='assistant.exe')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path "$root\build" | Out-Null
if($OutputName -notmatch '^[a-zA-Z0-9_-]+\.exe$'){throw 'OutputName must be a local executable filename'}
& $Compiler -std=c++20 -O1 -Wall -Wextra -Wno-unused-parameter -I "$root\vendor" "$root\src\main.cpp" -o "$root\build\$OutputName" -lsqlite3 -lssl -lcrypto -lws2_32 -lcrypt32
if($LASTEXITCODE -ne 0){throw 'C++ build failed'}
Write-Output "Built $root\build\$OutputName"
