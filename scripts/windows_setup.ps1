# citationHop — Windows setup & test script
# ===========================================
# One-shot PowerShell script for setting up citationHop on a fresh
# Windows machine.  Idempotent: re-running is safe.
#
# Usage (from the project root, in PowerShell):
#     .\scripts\windows_setup.ps1
#
# What it does:
#   1. Checks that Python 3.10+ is on PATH
#   2. Creates a .venv in the project root (if missing)
#   3. Installs the package in editable mode + dev/test deps
#   4. Runs the headless smoke test
#   5. Runs the full pytest suite
#   6. Prints next-step instructions for launching the GUI

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

# --- 1. Check Python ---------------------------------------------------
Write-Section "Checking Python"
try {
    $py = (python --version) 2>&1
    if ($LASTEXITCODE -ne 0) { throw "python not found" }
    Write-Host "  $py" -ForegroundColor Green
}
catch {
    Write-Host "  Python 3.10+ is required but not found on PATH." -ForegroundColor Red
    Write-Host "  Install from https://www.python.org/downloads/windows/" -ForegroundColor Yellow
    Write-Host "  IMPORTANT: tick 'Add Python to PATH' during install." -ForegroundColor Yellow
    exit 1
}

# --- 2. Create venv ----------------------------------------------------
Write-Section "Creating virtual environment"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "  created .venv" -ForegroundColor Green
} else {
    Write-Host "  .venv already exists, skipping" -ForegroundColor Yellow
}

# Activate for the rest of this script
& .\.venv\Scripts\Activate.ps1

# --- 3. Install --------------------------------------------------------
Write-Section "Installing citation-hop (editable) + dev deps"
python -m pip install --upgrade pip --quiet
python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  install failed" -ForegroundColor Red
    exit 1
}
Write-Host "  installed" -ForegroundColor Green

# --- 4. Smoke test -----------------------------------------------------
Write-Section "Headless smoke test"
python scripts/smoke_test.py
$smokeExit = $LASTEXITCODE
if ($smokeExit -ne 0) {
    Write-Host "  smoke test FAILED (exit $smokeExit)" -ForegroundColor Red
    Write-Host "  do NOT launch the GUI until this is fixed" -ForegroundColor Red
    exit $smokeExit
}

# --- 5. Full test suite ------------------------------------------------
Write-Section "Full pytest suite"
python -m pytest -q
$pytestExit = $LASTEXITCODE
if ($pytestExit -ne 0) {
    Write-Host "  pytest FAILED (exit $pytestExit)" -ForegroundColor Red
    exit $pytestExit
}

# --- 6. Next steps -----------------------------------------------------
Write-Section "All green. Next steps"
Write-Host ""
Write-Host "  Launch the GUI (system-tray icon):" -ForegroundColor White
Write-Host "    python -m citation_hop" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Or use the installed entry point:" -ForegroundColor White
Write-Host "    citation-hop" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Grant Windows permissions (one-time):" -ForegroundColor White
Write-Host "    Settings -> Privacy & security ->" -ForegroundColor Gray
Write-Host "      Background apps:    ON  (for tray icon)" -ForegroundColor Gray
Write-Host "      Notifications:      ON  (for status toasts)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Manual test checklist: see TESTING.md" -ForegroundColor White
Write-Host ""
