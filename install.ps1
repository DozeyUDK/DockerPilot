# Docker Pilot - One-Click Installation Script for Windows PowerShell
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

param(
    [switch]$Extras
)

Write-Host ""
Write-Host "====================================" -ForegroundColor Cyan
Write-Host " Docker Pilot Installation Script" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""
if ($Extras) {
    Write-Host "[*] Full stack mode enabled: DockerPilot CLI + DockerPilotExtras" -ForegroundColor Cyan
    Write-Host ""
}

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
    if ($LASTEXITCODE -ne 0) { throw "docker-not-ready" }
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
docker info *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Docker daemon is running" -ForegroundColor Green
} else {
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

# Install Docker Pilot (includes TUI dependencies)
Write-Host ""
Write-Host "[*] Installing Docker Pilot..." -ForegroundColor Yellow
python -m pip install -e ".[tui]"
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
dockerpilot --help *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Docker Pilot is ready!" -ForegroundColor Green
} else {
    Write-Host "[WARNING] Installation complete, but command verification failed" -ForegroundColor Yellow
    Write-Host "Try running: dockerpilot --help" -ForegroundColor White
}

Write-Host ""
Write-Host "Quick Start:" -ForegroundColor Cyan
Write-Host "  dockerpilot                    # Interactive mode" -ForegroundColor White
Write-Host "  dockerpilot --help             # Show help" -ForegroundColor White
Write-Host "  dockerpilot validate           # Check system" -ForegroundColor White
Write-Host "  dockerpilot tui                # Mouse-friendly TUI" -ForegroundColor White
if ($Extras) {
    Write-Host "" 
    Write-Host "DockerPilotExtras:" -ForegroundColor Cyan
    Write-Host "  cd DockerPilotExtras" -ForegroundColor White
    Write-Host "  python -m venv .venv" -ForegroundColor White
    Write-Host "  .venv\\Scripts\\pip install -r requirements.txt" -ForegroundColor White
    Write-Host "  cd frontend; npm install; cd .." -ForegroundColor White
    Write-Host "  .venv\\Scripts\\python loader.py" -ForegroundColor White
}
Write-Host ""
Write-Host "Documentation: README.md" -ForegroundColor Cyan
Write-Host ""

# Optional: Install GitPython
$installGit = Read-Host "Install GitPython for Git integration? (y/N)"
if ($installGit -eq "y" -or $installGit -eq "Y") {
    pip install GitPython
    Write-Host "[OK] GitPython installed" -ForegroundColor Green
}

if ($Extras) {
    Write-Host ""
    Write-Host "[*] Installing DockerPilotExtras backend dependencies..." -ForegroundColor Yellow
    Push-Location "DockerPilotExtras"
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create DockerPilotExtras virtual environment" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    .\.venv\Scripts\python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install DockerPilotExtras Python dependencies" -ForegroundColor Red
        Pop-Location
        exit 1
    }

    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($nodeCmd -and $npmCmd) {
        Write-Host "[*] Installing DockerPilotExtras frontend dependencies..." -ForegroundColor Yellow
        Push-Location "frontend"
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Failed to install DockerPilotExtras frontend dependencies" -ForegroundColor Red
            Pop-Location
            Pop-Location
            exit 1
        }
        Pop-Location
    } else {
        Write-Host "[WARNING] Node.js/npm not found. DockerPilotExtras frontend dependencies were not installed." -ForegroundColor Yellow
        Write-Host "          Install Node.js 18+ and run: cd DockerPilotExtras\\frontend && npm install" -ForegroundColor Yellow
    }

    Pop-Location
    Write-Host "[OK] DockerPilotExtras setup completed" -ForegroundColor Green
}

Write-Host ""
Write-Host "Setup complete! Happy deploying!" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"

