#!/usr/bin/env python3
"""
Setup verification script for DockerPilot Extras (Web Version)
Checks if all requirements are met before running the web application
"""

import sys
import subprocess
from pathlib import Path


def check_python_version():
    """Check Python version"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"❌ Python 3.8+ required. Found: {version.major}.{version.minor}")
        return False
    print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
    return True


def check_python_dependencies():
    """Check required Python packages"""
    required = ['flask', 'flask_cors', 'flask_restful', 'yaml']
    missing = []
    
    for package in required:
        try:
            package_import = package.replace('-', '_')
            __import__(package_import)
            print(f"✓ {package}")
        except ImportError:
            print(f"❌ {package} - missing")
            missing.append(package)
    
    if missing:
        print(f"\nInstall missing packages: pip install {' '.join(missing)}")
        print(f"Or install all: pip install -r requirements.txt")
        return False
    return True


def check_node():
    """Check Node.js availability"""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✓ Node.js: {result.stdout.strip()}")
            
            # Check npm
            result_npm = subprocess.run(
                ["npm", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result_npm.returncode == 0:
                print(f"✓ npm: {result_npm.stdout.strip()}")
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print("❌ Node.js/npm - not found")
    print("   Install Node.js: https://nodejs.org/")
    return False


def check_frontend_dependencies():
    """Check if frontend dependencies are installed"""
    frontend_dir = Path(__file__).parent / "frontend"
    node_modules = frontend_dir / "node_modules"
    
    if node_modules.exists():
        print("✓ Frontend dependencies installed")
        return True
    else:
        print("⚠ Frontend dependencies are not installed")
        print("   Run: cd frontend && npm install")
        return False


def check_docker():
    """Check Docker availability"""
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✓ Docker: {result.stdout.strip()}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print("❌ Docker - not found")
    print("   Install Docker: https://docs.docker.com/get-docker/")
    return False


def check_dockerpilot():
    """Check DockerPilot availability"""
    try:
        result = subprocess.run(
            ["dockerpilot", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            print(f"✓ DockerPilot: {version}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    print("❌ DockerPilot - not found")
    print("   Install DockerPilot: https://github.com/DozeyUDK/DockerPilot")
    return False


def main():
    """Run all checks"""
    print("DockerPilot Extras (Web) - Configuration Verification\n")
    print("=" * 60)
    
    checks = [
        ("Python", check_python_version, True),
        ("Python Dependencies", check_python_dependencies, True),
        ("Node.js/npm", check_node, True),
        ("Frontend Dependencies", check_frontend_dependencies, False),  # Warning only
        ("Docker", check_docker, True),
        ("DockerPilot", check_dockerpilot, True),
    ]
    
    results = []
    warnings = []
    
    for name, check_func, required in checks:
        print(f"\n{name}:")
        result = check_func()
        if required:
            results.append(result)
        elif not result:
            warnings.append(name)
    
    print("\n" + "=" * 60)
    
    if all(results):
        print("\n✅ All requirements met!")
        if warnings:
            print(f"⚠ Warnings: {', '.join(warnings)}")
        print("\nYou can run the application:")
        print("  Backend:  python run_dev.py")
        print("  Frontend: cd frontend && npm run dev")
        return 0
    else:
        print("\n❌ Some requirements are not met")
        print("Fix the issues above before running the application")
        return 1


if __name__ == "__main__":
    sys.exit(main())

