@echo off
REM Docker Pilot - One-Click Installation Script for Windows
REM PowerShell/CMD compatible

echo.
echo ====================================
echo  Docker Pilot Installation Script
echo ====================================
echo.

REM Check Python
echo [*] Checking prerequisites...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.9 or higher from https://www.python.org/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
echo [OK] Python %PYTHON_VERSION% found

REM Check Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Docker is not installed or not in PATH
    echo Docker is required for Docker Pilot to work.
    echo Please install Docker Desktop: https://docs.docker.com/desktop/windows/
    set /p CONTINUE="Continue anyway? (y/N): "
    if /i not "%CONTINUE%"=="y" exit /b 1
) else (
    echo [OK] Docker found
)

REM Check Docker daemon
docker info >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Docker daemon is not running
    echo Please start Docker Desktop and run this script again.
    set /p CONTINUE="Continue anyway? (y/N): "
    if /i not "%CONTINUE%"=="y" exit /b 1
) else (
    echo [OK] Docker daemon is running
)

REM Get script directory
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Install dependencies
echo.
echo [*] Installing dependencies...
if exist requirements.txt (
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed from requirements.txt
) else (
    echo [ERROR] requirements.txt not found
    pause
    exit /b 1
)

REM Install in development mode
echo.
echo [*] Installing Docker Pilot...
pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install Docker Pilot
    pause
    exit /b 1
)

echo.
echo [OK] Installation completed successfully!
echo.

REM Verify installation
echo [*] Verifying installation...
dockerpilot --help >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Installation complete, but command verification failed
    echo Try running: dockerpilot --help
) else (
    echo [OK] Docker Pilot is ready!
)

echo.
echo Quick Start:
echo   dockerpilot                    # Interactive mode
echo   dockerpilot --help             # Show help
echo   dockerpilot validate           # Check system
echo.
echo Documentation: README.md
echo.

REM Optional: Install GitPython
set /p INSTALL_GIT="Install GitPython for Git integration? (y/N): "
if /i "%INSTALL_GIT%"=="y" (
    pip install GitPython
    echo [OK] GitPython installed
)

echo.
echo Setup complete! Happy deploying!
echo.
pause

