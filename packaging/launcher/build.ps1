# Builds Runway's launcher with the C# compiler that ships with Windows - nothing to install.
# Output: build\launcher\Runway.exe  (build_release.py runs this when the launcher is missing)
param([string]$Out = (Join-Path $PSScriptRoot '..\..\build\launcher\Runway.exe'))

$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "C# compiler not found at $csc (.NET Framework 4.x)" }

$icon = Join-Path $PSScriptRoot '..\runway.ico'
$src = Join-Path $PSScriptRoot 'Launcher.cs'
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null

& $csc /nologo /target:winexe /optimize+ /platform:anycpu "/out:$Out" "/win32icon:$icon" `
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll $src
if ($LASTEXITCODE -ne 0) { throw "Launcher build failed ($LASTEXITCODE)" }
Write-Output "Built $((Resolve-Path $Out).Path) ($([math]::Round((Get-Item $Out).Length / 1KB)) KB)"
