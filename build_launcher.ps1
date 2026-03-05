$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python executable not found. Create .venv or install Python first."
    }
    $pythonExe = $pythonCommand.Source
}

$specPath = Join-Path $projectRoot "LiveLauncher.spec"
$iconPath = Join-Path $projectRoot "icon.ico"
$distDir = Join-Path $projectRoot "dist"
$buildDir = Join-Path $projectRoot "build"
$outputExe = Join-Path $distDir "LiveLauncher.exe"

if (-not (Test-Path $specPath)) {
    throw "Spec file not found: $specPath"
}

if (-not (Test-Path $iconPath)) {
    throw "Icon file not found: $iconPath"
}

Write-Host "Using Python: $pythonExe"

& $pythonExe -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not found. Installing..."
    & $pythonExe -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller."
    }
}

if (Test-Path $buildDir) {
    Remove-Item $buildDir -Recurse -Force
}

if (Test-Path $distDir) {
    Remove-Item $distDir -Recurse -Force
}

Write-Host "Building LiveLauncher.exe with icon..."
& $pythonExe -m PyInstaller --noconfirm --clean $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

if (-not (Test-Path $outputExe)) {
    throw "Build completed but output was not found: $outputExe"
}

Write-Host "Build completed: $outputExe"
