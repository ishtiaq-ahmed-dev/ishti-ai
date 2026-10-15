"""python -m ishti [port]  ->  web HUD + dispatcher on http://127.0.0.1:8888
   python -m ishti --version  ->  version, source revision and source hash, then exit (ishti/version.py)

Single instance (a second launch asks the first to show its window and exits), logging to the rotating file in
%LOCALAPPDATA%\\ISHTI\\logs (no console needed: pythonw works), and a Job Object so every child process dies with us."""
import sys

# Answered before anything boots: --version must work on a machine where the server cannot start, because
# that is exactly when someone needs to report which build they are running.
if sys.argv[1:2] in (["--version"], ["-V"]):
    from ishti import version

    print(version.describe())
    sys.exit(0)
if sys.argv[1:2] in (["--help"], ["-h"]):
    print(__doc__.strip())
    sys.exit(0)

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
