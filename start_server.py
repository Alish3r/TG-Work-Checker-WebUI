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
                        process_pid = int(parts[-1])
                        pids.add(process_pid)
                    except ValueError:
                        pass
        
        # Also kill all Python processes to be safe (they might be related)
        # This is more aggressive but prevents stale processes
        try:
            python_result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
                capture_output=True,
                text=True,
                check=False
            )
            for line in python_result.stdout.split('\n')[1:]:  # Skip header
                if 'python.exe' in line:
                    parts = line.split('","')
                    if len(parts) >= 2:
                        try:
                            process_pid = int(parts[1].strip('"'))
                            pids.add(process_pid)
                        except (ValueError, IndexError):
                            pass
        except Exception:
            pass  # If tasklist fails, continue with netstat PIDs only
        
        # Kill the processes
        killed_count = 0
        for process_pid in pids:
            try:
                subprocess.run(['taskkill', '/F', '/PID', str(process_pid)], 
                             capture_output=True, check=False, timeout=5)
                killed_count += 1
            except Exception as e:
                print(f"Could not kill process {process_pid}: {e}")
        
        if killed_count > 0:
            print(f"Killed {killed_count} process(es) on port {port}")
        
        # Wait for ports to be released
        time.sleep(3)
    except Exception as e:
        print(f"Error killing processes: {e}")

def clear_python_cache():
    """Clear Python bytecode cache to ensure fresh imports."""
    import shutil
    cache_dirs = ['__pycache__']
    for root, dirs, files in os.walk('.'):
        if '__pycache__' in dirs:
            cache_path = os.path.join(root, '__pycache__')
            try:
                shutil.rmtree(cache_path)
            except Exception:
                pass
        # Also remove .pyc files
        for file in files:
            if file.endswith('.pyc'):
                try:
                    os.remove(os.path.join(root, file))
                except Exception:
                    pass

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Start the TG Work Checker web server')
    parser.add_argument('--port', type=int, default=None, 
                       help='Port to run on (default: from PORT env var or 8001)')
    parser.add_argument('--no-cache-clear', action='store_true',
                       help='Skip clearing Python cache (faster but may use stale code)')
    args = parser.parse_args()
    
    # Determine port: CLI arg > env var > default
    port = args.port or int(os.getenv('PORT', '8001'))
    
    print("=" * 60)
    print("TG Work Checker - Server Startup")
    print("=" * 60)
    
    # Step 1: Kill all Python processes (aggressive cleanup)
    print("\n[1/4] Cleaning up old processes...")
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
            capture_output=True,
            text=True,
            check=False
        )
        python_pids = []
        for line in result.stdout.split('\n')[1:]:
            if 'python.exe' in line:
                parts = line.split('","')
                if len(parts) >= 2:
                    try:
                        process_pid = int(parts[1].strip('"'))
                        python_pids.append(process_pid)
                    except (ValueError, IndexError):
                        pass
        
        if python_pids:
            print(f"  Found {len(python_pids)} Python process(es), killing...")
            for process_pid in python_pids:
                try:
                    subprocess.run(['taskkill', '/F', '/PID', str(process_pid)], 
                                 capture_output=True, check=False, timeout=2)
                except Exception:
                    pass
            time.sleep(2)
        else:
            print("  No Python processes found")
    except Exception as e:
        print(f"  Warning: Could not list processes: {e}")
    
    # Step 2: Clear port
    print(f"\n[2/4] Checking port {port}...")
    if is_port_in_use(port):
        print(f"  Port {port} is in use. Attempting to free it...")
        kill_processes_on_port(port)
        time.sleep(2)
        
        if is_port_in_use(port):
            print(f"\n  ERROR: Port {port} is still in use after cleanup.")
            print(f"  Try: python start_server.py --port {port + 1}")
            sys.exit(1)
    print(f"  Port {port} is free")
    
    # Step 3: Clear Python cache
    if not args.no_cache_clear:
        print("\n[3/4] Clearing Python cache...")
        clear_python_cache()
        print("  Cache cleared")
    else:
        print("\n[3/4] Skipping cache clear (--no-cache-clear)")
    
    # Step 4: Start server
    print(f"\n[4/4] Starting server on port {port}...")
    print(f"  Server will be available at: http://localhost:{port}/")
    print("  Press Ctrl+C to stop the server")
    print("=" * 60 + "\n")
    
    # Set environment to prevent bytecode caching
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    
    # Start uvicorn
    os.chdir(Path(__file__).parent)
    cmd = [
        sys.executable, '-m', 'uvicorn',
        'web_app:app',
        '--host', '0.0.0.0',
        '--port', str(port),
        '--reload',
        '--reload-dir', '.',  # Watch current directory
        '--reload-include', '*.py',  # Only watch Python files
    ]
    
    subprocess.run(cmd)

if __name__ == '__main__':
    main()
