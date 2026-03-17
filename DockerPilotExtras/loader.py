#!/usr/bin/env python3
"""
DockerPilot Extras - Loader Script
Starts backend (Flask) and frontend (npm) simultaneously.
Interrupting the script (Ctrl+C) stops both servers.
"""

import os
import sys
import subprocess
import signal
import time
import socket
import shutil
from pathlib import Path

# Processes to manage
backend_process = None
frontend_process = None
backend_port = 5000  # Default port


def signal_handler(sig, frame):
    """Handle interruption (Ctrl+C)"""
    print("\n\n🛑 Stopping servers...")
    
    if frontend_process and frontend_process.poll() is None:
        print("Stopping frontend (npm)...")
        try:
            # Send SIGTERM to process and all its children
            if sys.platform == 'win32':
                frontend_process.terminate()
            else:
                try:
                    os.killpg(os.getpgid(frontend_process.pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    # Process no longer exists or has no group
                    frontend_process.terminate()
        except (OSError, ProcessLookupError) as e:
            # Process no longer exists
            pass
        except Exception as e:
            print(f"Error stopping frontend: {e}")
    
    if backend_process and backend_process.poll() is None:
        print("Stopping backend (Flask)...")
        try:
            backend_process.terminate()
        except (OSError, ProcessLookupError):
            # Process no longer exists
            pass
        except Exception as e:
            print(f"Error stopping backend: {e}")
    
    # Wait for processes to finish
    if frontend_process and frontend_process.poll() is None:
        try:
            frontend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Forcing frontend termination...")
            try:
                if sys.platform != 'win32':
                    try:
                        os.killpg(os.getpgid(frontend_process.pid), signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        frontend_process.kill()
                else:
                    frontend_process.kill()
            except (OSError, ProcessLookupError):
                pass
        except (OSError, ProcessLookupError):
            pass
    
    if backend_process and backend_process.poll() is None:
        try:
            backend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Forcing backend termination...")
            try:
                backend_process.kill()
            except (OSError, ProcessLookupError):
                pass
        except (OSError, ProcessLookupError):
            pass
    
    print("✅ All servers stopped.")
    sys.exit(0)


def check_dependencies():
    """Checks if required tools are available"""
    errors = []
    node_version = None
    
    # Check Node.js/npm
    try:
        result = subprocess.run(['node', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            errors.append("Node.js is not available")
        else:
            node_version = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("Node.js is not installed")
    
    # Check npm
    try:
        result = subprocess.run(['npm', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            errors.append("npm is not available")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("npm is not installed")
    
    # Vite 5 requires a modern Node.js (18+). Fail fast with a clear message.
    if node_version:
        try:
            v = node_version.lstrip('v').split('.', 2)
            major = int(v[0]) if v and v[0].isdigit() else None
        except Exception:
            major = None

        if major is None:
            errors.append(f"Unable to parse Node.js version: {node_version}")
        elif major < 18:
            errors.append(f"Node.js {node_version} is too old for the frontend (requires Node.js 18+).")

    # Check Python dependencies
    try:
        import flask
        import flask_cors
        import flask_restful
    except ImportError as e:
        errors.append(f"Missing Python dependency: {e.name}")
    
    if errors:
        print("❌ Configuration errors:")
        for error in errors:
            print(f"   - {error}")
        print("\nInstall missing dependencies:")
        print("   1. Run setup script (creates venv, installs Python deps):")
        print("      chmod +x setup_extras.sh && ./setup_extras.sh")
        print("   2. Then start with: .venv/bin/python loader.py")
        print("   Node.js (if missing/too old): install Node.js 18+ (recommended: NodeSource or nvm).")
        return False
    
    return True


def is_port_available(port):
    """Checks if port is available"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False


def ask_for_port():
    """Asks user for a new port"""
    while True:
        try:
            port_input = input("\n🔧 Port is in use. Enter a new port (or Enter for 5001): ").strip()
            if not port_input:
                port_input = "5001"
            
            port = int(port_input)
            
            if port < 1024 or port > 65535:
                print("❌ Port must be in range 1024-65535")
                continue
            
            if not is_port_available(port):
                print(f"❌ Port {port} is also in use. Try another one.")
                continue
            
            return port
        except ValueError:
            print("❌ Invalid port number. Enter a number.")
        except KeyboardInterrupt:
            print("\n\nCancelled.")
            sys.exit(0)


def start_backend():
    """Starts Flask backend"""
    global backend_process, backend_port
    
    backend_dir = Path(__file__).parent
    os.environ.setdefault('FLASK_ENV', 'development')
    
    # Check if default port is available
    if not is_port_available(backend_port):
        print(f"⚠️  Port {backend_port} is in use.")
        backend_port = ask_for_port()
    
    os.environ['PORT'] = str(backend_port)
    
    print(f"🚀 Starting backend (Flask) on port {backend_port}...")
    
    try:
        backend_process = subprocess.Popen(
            [sys.executable, str(backend_dir / 'run_dev.py')],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Collect output and detect errors
        output_lines = []
        port_error_detected = False
        
        def collect_backend_output():
            nonlocal port_error_detected
            if backend_process and backend_process.stdout:
                for line in iter(backend_process.stdout.readline, ''):
                    if line:
                        output_lines.append(line.rstrip())
                        print(f"[BACKEND] {line.rstrip()}")
                        
                        # Detect port-related errors
                        line_lower = line.lower()
                        if ('address already in use' in line_lower or 
                            ('port' in line_lower and 'is in use' in line_lower)):
                            port_error_detected = True
        
        import threading
        backend_thread = threading.Thread(target=collect_backend_output, daemon=True)
        backend_thread.start()
        
        # Wait a moment to check if process started or error occurred
        time.sleep(3)
        
        if port_error_detected or backend_process.poll() is not None:
            # Stop process if it didn't start
            if backend_process.poll() is None:
                backend_process.terminate()
                backend_process.wait(timeout=2)
            
            # Port was in use - ask for new one
            if port_error_detected:
                backend_port = ask_for_port()
                os.environ['PORT'] = str(backend_port)
                
                # Restart with new port
                print(f"\n🔄 Attempting to start on port {backend_port}...")
                return start_backend()  # Recursive call with new port
            else:
                print("❌ Backend did not start correctly!")
                return False
        
        print(f"✅ Backend started on http://localhost:{backend_port}")
        return True
        
    except Exception as e:
        print(f"❌ Error starting backend: {e}")
        return False


def fix_rollup_dependencies(frontend_dir):
    """Fixes rollup dependencies issue - removes node_modules and package-lock.json, reinstalls"""
    print("🔧 Rollup issue detected. Fixing dependencies...")
    
    try:
        # Remove package-lock.json
        package_lock = frontend_dir / 'package-lock.json'
        if package_lock.exists():
            package_lock.unlink()
            print("   ✓ Removed package-lock.json")
        
        # Remove node_modules
        node_modules = frontend_dir / 'node_modules'
        if node_modules.exists():
            shutil.rmtree(node_modules)
            print("   ✓ Removed node_modules")
        
        # Reinstall dependencies
        print("   📦 Reinstalling dependencies...")
        install_result = subprocess.run(
            ['npm', 'install'],
            cwd=str(frontend_dir),
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if install_result.returncode != 0:
            print(f"❌ Error during reinstallation:\n{install_result.stderr}")
            return False
        
        print("✅ Dependencies fixed")
        return True
        
    except Exception as e:
        print(f"❌ Error fixing dependencies: {e}")
        return False


def start_frontend():
    """Starts npm dev server"""
    global frontend_process
    
    frontend_dir = Path(__file__).parent / 'frontend'
    
    if not frontend_dir.exists():
        print("❌ Frontend directory does not exist!")
        return False
    
    # Check if node_modules exists
    if not (frontend_dir / 'node_modules').exists():
        print("⚠️  node_modules not found. Installing dependencies...")
        try:
            install_result = subprocess.run(
                ['npm', 'install'],
                cwd=str(frontend_dir),
                capture_output=True,
                text=True,
                timeout=120
            )
            if install_result.returncode != 0:
                print(f"❌ Error installing dependencies:\n{install_result.stderr}")
                return False
            print("✅ Dependencies installed")
        except subprocess.TimeoutExpired:
            print("❌ Dependency installation timeout exceeded")
            return False
        except Exception as e:
            print(f"❌ Error during installation: {e}")
            return False
    
    # Fix permissions for vite and other binaries if needed
    bin_dir = frontend_dir / 'node_modules' / '.bin'
    if bin_dir.exists():
        try:
            # Fix permissions for all files in .bin
            for bin_file in bin_dir.iterdir():
                if bin_file.is_file():
                    os.chmod(bin_file, 0o755)
        except Exception:
            pass  # Ignore permission errors
    
    print("🚀 Starting frontend (npm)...")
    
    try:
        # Ustaw zmienną środowiskową dla portu backendu
        frontend_env = os.environ.copy()
        frontend_env['VITE_BACKEND_PORT'] = str(backend_port)
        frontend_env['BACKEND_PORT'] = str(backend_port)
        
        # Set new process group for frontend (Unix only)
        kwargs = {}
        if sys.platform != 'win32':
            kwargs['preexec_fn'] = os.setsid
        
        frontend_process = subprocess.Popen(
            ['npm', 'run', 'dev'],
            cwd=str(frontend_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=frontend_env,
            **kwargs
        )
        
        # Collect output and detect rollup errors and frontend port
        output_lines = []
        rollup_error_detected = False
        frontend_port = None
        network_address = None
        
        def collect_frontend_output():
            nonlocal rollup_error_detected, frontend_port, network_address
            if frontend_process and frontend_process.stdout:
                for line in iter(frontend_process.stdout.readline, ''):
                    if line:
                        output_lines.append(line.rstrip())
                        print(f"[FRONTEND] {line.rstrip()}")
                        
                        # Detect frontend port from Vite output
                        # Format: "➜  Local:   http://localhost:3001/"
                        import re
                        port_match = re.search(r'Local:\s+http://localhost:(\d+)', line)
                        if port_match:
                            frontend_port = int(port_match.group(1))
                        
                        # Detect network address from Vite output
                        # Format: "➜  Network: http://192.168.0.58:3001/"
                        network_match = re.search(r'Network:\s+http://([\d.]+):(\d+)', line)
                        if network_match:
                            network_address = f"http://{network_match.group(1)}:{network_match.group(2)}"
                        
                        # Detect rollup errors
                        line_lower = line.lower()
                        if ('@rollup/rollup' in line_lower and 'cannot find module' in line_lower) or \
                           ('rollup-linux' in line_lower and 'cannot find module' in line_lower) or \
                           ('npm has a bug related to optional dependencies' in line_lower) or \
                           ('@rollup/rollup-linux-x64-gnu' in line_lower):
                            rollup_error_detected = True
        
        import threading
        frontend_thread = threading.Thread(target=collect_frontend_output, daemon=True)
        frontend_thread.start()
        
        # Wait a moment to check if process started or error occurred
        # Give more time to detect rollup error
        max_wait_time = 6
        waited = 0
        while waited < max_wait_time and frontend_process.poll() is None and not rollup_error_detected:
            time.sleep(0.5)
            waited += 0.5
        
        # Check if process exited (error) or rollup error detected
        process_exited = frontend_process.poll() is not None
        
        # If process exited, check output even if error wasn't detected in real-time
        if process_exited and not rollup_error_detected:
            output_text = '\n'.join(output_lines).lower()
            if ('@rollup/rollup' in output_text and 'cannot find module' in output_text) or \
               ('rollup-linux' in output_text and 'cannot find module' in output_text) or \
               ('npm has a bug related to optional dependencies' in output_text) or \
               ('@rollup/rollup-linux-x64-gnu' in output_text):
                rollup_error_detected = True
        
        if process_exited or rollup_error_detected:
            # If process still running but rollup error detected, stop it
            if not process_exited and rollup_error_detected:
                frontend_process.terminate()
                try:
                    frontend_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    frontend_process.kill()
            
            # Rollup error detected - fix dependencies and try again
            if rollup_error_detected:
                if fix_rollup_dependencies(frontend_dir):
                    print("\n🔄 Attempting to restart frontend...")
                    return start_frontend()  # Recursive call after fix
                else:
                    print("❌ Failed to fix rollup dependencies")
                    return False
            elif process_exited:
                # Process exited with other error
                print("❌ Frontend did not start correctly!")
                if output_lines:
                    print("   Last output lines:")
                    for line in output_lines[-5:]:
                        print(f"   {line}")
                return False
        
        # Display port information
        if frontend_port:
            print(f"✅ Frontend started on http://localhost:{frontend_port}")
            if network_address:
                print(f"   Also available at: {network_address}")
            print(f"   Backend running on http://localhost:{backend_port}")
            print(f"\n   🌐 Open in browser:")
            print(f"      Local: http://localhost:{frontend_port}")
            if network_address:
                print(f"      Network: {network_address}")
        else:
            print("✅ Frontend started (check port in output above)")
            print(f"   Backend running on http://localhost:{backend_port}")
        return True
        
    except Exception as e:
        print(f"❌ Error starting frontend: {e}")
        return False


def main():
    """Main function"""
    print("=" * 60)
    print("DockerPilot Extras - Loader")
    print("=" * 60)
    print()
    
    # Register handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    print()
    
    # Start backend
    if not start_backend():
        sys.exit(1)
    
    print()
    
    # Start frontend
    frontend_ok = start_frontend()
    if not frontend_ok:
        print("\n⚠️  Frontend did not start, but backend is running.")
        print("   You can start frontend manually: cd frontend && npm run dev")
    
    print()
    print("=" * 60)
    if frontend_ok:
        print("✅ Both servers are running!")
    else:
        print("⚠️  Backend is running (frontend failed)")
    print("=" * 60)
    print("\nPress Ctrl+C to stop both servers\n")
    
    # Wait for termination (or interruption)
    try:
        while True:
            # Check if processes are still running
            if backend_process and backend_process.poll() is not None:
                print("\n⚠️  Backend terminated unexpectedly")
                break
            
            if frontend_process and frontend_process.poll() is not None:
                print("\n⚠️  Frontend terminated unexpectedly")
                break
            
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    
    # Call handler to stop processes
    signal_handler(None, None)


if __name__ == '__main__':
    main()

