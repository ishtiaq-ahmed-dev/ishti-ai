"""python -m ishti [port]  ->  web HUD + dispatcher on http://127.0.0.1:8888

Single instance (a second launch asks the first to show its window and exits), logging to the rotating file in
%LOCALAPPDATA%\\ISHTI\\logs (no console needed: pythonw works), and a Job Object so every child process dies with us."""
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888

from ishti import privacy

privacy.install()  # egress guard before anything can open a socket (harden() already ran in ishti/__init__)

from ishti import lifecycle

if not lifecycle.single_instance(port):
    print(f"ISHTI is already running on port {port}; asked it to show its window.")
    sys.exit(0)
lifecycle.setup_logging()
lifecycle.adopt_job()
lifecycle.install_launch_patches()  # apps ISHTI opens for the owner outlive it; console children don't flash windows

from interface.web_ui.server import start_server

start_server(port)
