#!/usr/bin/env python3
"""
Server startup script that ensures clean shutdown of old processes
and starts a fresh server.
"""
import os
import sys
import subprocess
import time
import socket
from pathlib import Path

def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return False
        except OSError:
            return True

def kill_processes_on_port(port: int):
    """Kill all processes using the specified port (Windows)."""
    try:
        # Find processes using the port
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True,
            text=True,
            check=True
        )
        
        pids = set()
        for line in result.stdout.split('\n'):
            if f':{port}' in line and 'LISTENING' in line:
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        pid = int(parts[-1])
                        pids.add(pid)
                    except ValueError:
                        pass
        
        # Kill the processes
        for pid in pids:
            try:
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                             capture_output=True, check=False)
                print(f"Killed process {pid}")
            except Exception as e:
                print(f"Could not kill process {pid}: {e}")
        
        # Wait a bit for ports to be released
        time.sleep(2)
    except Exception as e:
        print(f"Error killing processes: {e}")

def main():
    port = int(os.getenv('PORT', '8000'))
    
    print(f"Checking port {port}...")
    if is_port_in_use(port):
        print(f"Port {port} is in use. Attempting to free it...")
        kill_processes_on_port(port)
        time.sleep(1)
        
        if is_port_in_use(port):
            print(f"ERROR: Port {port} is still in use after cleanup.")
            print("Please manually stop processes using this port or use a different port.")
            sys.exit(1)
    
    print(f"Port {port} is free. Starting server...")
    
    # Start uvicorn
    os.chdir(Path(__file__).parent)
    cmd = [
        sys.executable, '-m', 'uvicorn',
        'web_app:app',
        '--host', '0.0.0.0',
        '--port', str(port),
        '--reload'
    ]
    
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)

if __name__ == '__main__':
    main()
