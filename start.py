"""
AION — Unified Platform Master Launcher
Global PayPal AI Hackathon 2026 Submission

Launches all platform engines concurrently with 1 command:
  1. AION Master Command Portal (Vite on Port 5173)
  2. AURA Financial Intelligence Workbench (FastAPI on Port 8000)
  3. FORGEX Autonomous Engineering Studio (Next.js on Port 3000)
  4. FORGEX Reliability Backend (FastAPI on Port 8001)

Usage:
  python start.py
"""

import sys
import os
import subprocess
import time
import signal
import webbrowser

# Ensure UTF-8 output on all operating systems including Windows cp1252
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICES = [
    {
        "name": "AURA Financial Workbench",
        "port": 8000,
        "url": "http://127.0.0.1:8000",
        "cwd": os.path.join(ROOT_DIR, "aura_platform"),
        "cmd": [sys.executable, "run_server.py"]
    },
    {
        "name": "FORGEX Reliability Backend",
        "port": 8001,
        "url": "http://127.0.0.1:8001",
        "cwd": os.path.join(ROOT_DIR, "forgex", "backend"),
        "cmd": [sys.executable, "run_forgex_backend.py"]
    },
    {
        "name": "FORGEX Autonomous Studio",
        "port": 3000,
        "url": "http://localhost:3000",
        "cwd": os.path.join(ROOT_DIR, "forgex", "AEGISNOVA"),
        "cmd": ["npm", "run", "dev"] if os.name != "nt" else ["cmd", "/c", "npm", "run", "dev"]
    },
    {
        "name": "AION Main Portal",
        "port": 5173,
        "url": "http://localhost:5173",
        "cwd": os.path.join(ROOT_DIR, "AION-Main-Platform"),
        "cmd": ["npm", "run", "dev"] if os.name != "nt" else ["cmd", "/c", "npm", "run", "dev"]
    }
]

BANNER = """
================================================================================
   AION - Autonomous Agentic Commerce & AURA Financial Intelligence
   PayPal AI Hackathon 2026 Submission
================================================================================
"""

def print_dashboard():
    print(BANNER)
    print("  ALL PLATFORM ENGINES BOOTED UNDER SINGLE SUPERVISED RUNTIME:\n")
    print("  [1] AION Master Portal    ->  http://localhost:5173")
    print("      Unified Hackathon Command Center, Hero & Navigation Hub\n")
    print("  [2] AURA AG Grid OS       ->  http://localhost:5173/aura")
    print("      AG Grid Sponsor Track • 6 Modes • PayPal Sandbox Active\n")
    print("  [3] FORGEX Studio         ->  http://localhost:5173/forgex")
    print("      Agentic Commerce Track • Autonomous Build • Evidence Warrants\n")
    print("  [4] Postman & API Specs   ->  http://127.0.0.1:8000/docs")
    print("      OpenAPI 3.0 Contract • APIMatic Verified Collection\n")
    print("  " + "-" * 78)
    print("  [JUDGE QUICK-DEMO]: Open http://localhost:5173 in browser")
    print("  [SHUTDOWN]: Press Ctrl+C at any time to terminate all services cleanly.")
    print("  " + "-" * 78 + "\n")

processes = []

def cleanup(sig=None, frame=None):
    print("\n\n  Shutting down AION Unified Platform...")
    for p, svc in processes:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        except Exception:
            pass
    print("  All services terminated cleanly. Goodbye!\n")
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("\n  Initializing AION Unified Platform engines...")

    for svc in SERVICES:
        if not os.path.isdir(svc["cwd"]):
            print(f"  Warning: Directory '{svc['cwd']}' not found, skipping {svc['name']}.")
            continue
        try:
            p = subprocess.Popen(
                svc["cmd"],
                cwd=svc["cwd"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            processes.append((p, svc))
            print(f"  + Started {svc['name']} (Port {svc['port']})")
        except Exception as e:
            print(f"  x Failed to start {svc['name']}: {e}")

    time.sleep(2)
    print_dashboard()

    if "--open" in sys.argv:
        time.sleep(1)
        webbrowser.open("http://localhost:5173")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
