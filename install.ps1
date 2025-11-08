# Docker Pilot - One-Click Installation Script for Windows PowerShell
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host " Docker Pilot Installation Script" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
Write-Host "[*] Checking prerequisites..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[OK] $pythonVersion found" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH" -ForegroundColor Red
    Write-Host "Please install Python 3.9 or higher from https://www.python.org/" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Check Docker
Write-Host "[*] Checking Docker..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version 2>&1
    Write-Host "[OK] Docker found: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Docker is not installed or not in PATH" -ForegroundColor Yellow
    Write-Host "Docker is required for Docker Pilot to work." -ForegroundColor Yellow
    Write-Host "Please install Docker Desktop: https://docs.docker.com/desktop/windows/" -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        exit 1
    }
}

# Check Docker daemon
Write-Host "[*] Checking Docker daemon..." -ForegroundColor Yellow
try {
    docker info | Out-Null
    Write-Host "[OK] Docker daemon is running" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Docker daemon is not running" -ForegroundColor Yellow
    Write-Host "Please start Docker Desktop and run this script again." -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        exit 1
    }
}

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Install Docker Pilot (includes all dependencies)
Write-Host ""
Write-Host "[*] Installing Docker Pilot..." -ForegroundColor Yellow
pip install -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install Docker Pilot" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "[OK] Installation completed successfully!" -ForegroundColor Green
Write-Host ""

# Verify installation
Write-Host "[*] Verifying installation..." -ForegroundColor Yellow
try {
    dockerpilot --help | Out-Null
    Write-Host "[OK] Docker Pilot is ready!" -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Installation complete, but command verification failed" -ForegroundColor Yellow
    Write-Host "Try running: dockerpilot --help" -ForegroundColor White
}

Write-Host ""
Write-Host "Quick Start:" -ForegroundColor Cyan
Write-Host "  dockerpilot                    # Interactive mode" -ForegroundColor White
Write-Host "  dockerpilot --help             # Show help" -ForegroundColor White
Write-Host "  dockerpilot validate           # Check system" -ForegroundColor White
Write-Host ""
Write-Host "Documentation: README.md" -ForegroundColor Cyan
Write-Host ""

# Optional: Install GitPython
$installGit = Read-Host "Install GitPython for Git integration? (y/N)"
if ($installGit -eq "y" -or $installGit -eq "Y") {
    pip install GitPython
    Write-Host "[OK] GitPython installed" -ForegroundColor Green
}

Write-Host ""
Write-Host "Setup complete! Happy deploying!" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"

