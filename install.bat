@echo off
REM Docker Pilot - One-Click Installation Script for Windows
REM PowerShell/CMD compatible

set INSTALL_EXTRAS=0
if /i "%~1"=="extras" set INSTALL_EXTRAS=1

echo.
echo ====================================
echo  Docker Pilot Installation Script
echo ====================================
echo.
if "%INSTALL_EXTRAS%"=="1" (
    echo [*] Full stack mode enabled: DockerPilot CLI + DockerPilotExtras
    echo.
)

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

REM Install Docker Pilot (includes TUI dependencies)
echo.
echo [*] Installing Docker Pilot...
python -m pip install -e ".[tui]"
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
echo   dockerpilot tui                # Mouse-friendly TUI
if "%INSTALL_EXTRAS%"=="1" (
    echo.
    echo DockerPilotExtras:
    echo   cd DockerPilotExtras
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo   cd frontend ^&^& npm install ^&^& cd ..
    echo   .venv\Scripts\python loader.py
)
echo.
echo Documentation: README.md
echo.

REM Optional: Install GitPython
set /p INSTALL_GIT="Install GitPython for Git integration? (y/N): "
if /i "%INSTALL_GIT%"=="y" (
    pip install GitPython
    echo [OK] GitPython installed
)

if "%INSTALL_EXTRAS%"=="1" (
    echo.
    echo [*] Installing DockerPilotExtras backend dependencies...
    pushd DockerPilotExtras
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create DockerPilotExtras virtual environment
        popd
        exit /b 1
    )
    .venv\Scripts\python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install DockerPilotExtras Python dependencies
        popd
        exit /b 1
    )

    node --version >nul 2>&1
    if errorlevel 1 (
        echo [WARNING] Node.js/npm not found. DockerPilotExtras frontend dependencies were not installed.
        echo           Install Node.js 18+ and run: cd DockerPilotExtras\frontend ^&^& npm install
    ) else (
        npm --version >nul 2>&1
        if errorlevel 1 (
            echo [WARNING] npm not found. DockerPilotExtras frontend dependencies were not installed.
            echo           Install Node.js 18+ and run: cd DockerPilotExtras\frontend ^&^& npm install
        ) else (
            echo [*] Installing DockerPilotExtras frontend dependencies...
            pushd frontend
            npm install
            if errorlevel 1 (
                echo [ERROR] Failed to install DockerPilotExtras frontend dependencies
                popd
                popd
                exit /b 1
            )
            popd
        )
    )
    popd
    echo [OK] DockerPilotExtras setup completed
)

echo.
echo Setup complete! Happy deploying!
echo.
pause

