#!/usr/bin/env python3
"""
Development server runner for DockerPilot Extras
Runs Flask backend and optionally builds/serves React frontend
"""

import os
import sys
from pathlib import Path

# Set environment variables for development
os.environ.setdefault('FLASK_ENV', 'development')
os.environ.setdefault('PORT', '5000')

if __name__ == '__main__':
    # Change to backend directory
    backend_dir = Path(__file__).parent / 'backend'
    sys.path.insert(0, str(backend_dir.parent))
    
    # Import and run Flask app
    from backend.app import app
    
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    print(f"Starting DockerPilot Extras backend on http://0.0.0.0:{port}")
    print(f"Debug mode: {debug}")
    print(f"\nFor frontend development, run in separate terminal:")
    print(f"  cd frontend && npm run dev")
    print(f"\nPress Ctrl+C to stop")
    
    app.run(host='0.0.0.0', port=port, debug=debug)

