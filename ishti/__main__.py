"""python -m ishti [port]  ->  web HUD + dispatcher on http://127.0.0.1:8888"""
import sys

from interface.web_ui.server import start_server

start_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8888)
