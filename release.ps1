<#
.SYNOPSIS
    Build the MF Portfolio Tracker portable exe + Windows installer, and
    optionally publish a GitHub release.

.DESCRIPTION
    One command to go from source to shippable artifacts:
      1. (re)generate the app icon
      2. build the standalone exe with PyInstaller
      3. compile the installer with Inno Setup (version stamped from -Version)
      4. with -Publish, create/push a git tag and a GitHub release with both
         binaries attached

.EXAMPLE
    .\release.ps1 -Version 1.0.1
    Builds dist\ and installer_output\ locally (no publish).

.EXAMPLE
    .\release.ps1 -Version 1.0.1 -Publish
    Builds, tags v1.0.1, pushes, and creates the GitHub release.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [switch]$Publish
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Normalise: accept either "1.0.1" or "v1.0.1".
$Version = $Version.TrimStart("v", "V")
$tag = "v$Version"
Write-Host "==> Building MF Portfolio Tracker $Version" -ForegroundColor Cyan

function Resolve-Tool([string[]]$candidates, [string]$name) {
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Could not find $name. Looked in: $($candidates -join ', ')"
}

# --- 1. icon ---------------------------------------------------------
Write-Host "==> Generating icon" -ForegroundColor Cyan
python make_icon.py

# --- 2. PyInstaller --------------------------------------------------
Write-Host "==> Building standalone exe (PyInstaller)" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean MFPortfolioTracker.spec
$exe = "dist\MF Portfolio Tracker.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }

# --- 3. Inno Setup ---------------------------------------------------
Write-Host "==> Compiling installer (Inno Setup)" -ForegroundColor Cyan
$iscc = Resolve-Tool @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) "ISCC.exe"
& $iscc "/DMyAppVersion=$Version" installer.iss
$setup = "installer_output\MFPortfolioTracker-Setup-$Version.exe"
if (-not (Test-Path $setup)) { throw "Inno Setup did not produce $setup" }

Write-Host "`nArtifacts:" -ForegroundColor Green
Write-Host "  $exe"
Write-Host "  $setup"

# --- 4. publish ------------------------------------------------------
if ($Publish) {
    Write-Host "==> Publishing GitHub release $tag" -ForegroundColor Cyan
    $gh = Resolve-Tool @("C:\Program Files\GitHub CLI\gh.exe") "gh"

    # Tag the current commit if the tag doesn't already exist.
    $existing = git tag --list $tag
    if (-not $existing) {
        git tag -a $tag -m "MF Portfolio Tracker $Version"
        git push origin $tag
    }

    & $gh release create $tag $setup $exe `
        --title "MF Portfolio Tracker $Version" `
        --notes "Automated release $tag. See README for install notes (unsigned build; SmartScreen 'More info -> Run anyway')."
    Write-Host "Release published." -ForegroundColor Green
}
else {
    Write-Host "`n(Skipped publish. Re-run with -Publish to create the GitHub release.)" -ForegroundColor Yellow
}
