# Setup

For a normal install follow the three steps in the [README](../README.md). This page is for people who want to know what the
installer does, or who set things up by hand.

## What the installer does (`install.bat` -> `tools/install.py`)
1. Checks Windows, free disk space, the NVIDIA card and the WebView2 runtime, and that `interface/app/dist` (the dashboard) exists.
2. Finds Python 3.11 (offers `winget install Python.Python.3.11` if it is missing) and creates `venv\`.
3. Installs `requirements.txt` (PyTorch comes from the CUDA 12.8 index so a CPU-only build is never picked) and `chatterbox-tts`
   with `--no-deps` (its torch pin would break the GPU build).
4. Checks that Ollama (0.34.2 or newer) is installed, starts it if needed, and pulls only the models the machine needs: the
   planner chosen for your graphics-card memory, the translator and the embedding model. It prints the download size first and asks.
5. Downloads the voice and speech-recognition files, creates the single desktop icon "ISHTI", and prints "All done".

`python tools/install.py --check` only reports what is present or missing (read-only, takes a fraction of a second).
Running the installer twice is safe.

## By hand
```bat
py -3.11 -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install --no-deps chatterbox-tts==0.1.7
ollama pull maternion/spark-x2.5:4b-q8_0
ollama create ishti-spark2.5-q8:4b -f ishti\spark2.5-q8.Modelfile
ollama pull gemma3:4b
ollama pull nomic-embed-text
venv\Scripts\python desktop\create_shortcut.py
```

## Running, stopping, logs
- Start: double-click the ISHTI icon (or `start.bat`). The dashboard is at `http://127.0.0.1:8888`.
- Stop: the Shut down button in the app (or `stop.bat`). Closing the window asks first.
- Logs: `%LOCALAPPDATA%\ISHTI\logs\ishti.log`. Your data (memory, chats, settings) is in `data\`; nothing leaves the PC.
- The project can live in any folder: the absolute `D:\ISHTI` paths in `ishti/config.json` are rebased to wherever it is.

## Tests
`venv\Scripts\python -m pytest` runs the offline suite (a few minutes; no GPU needed beyond what some tests load). Tests marked
`e2e` need Ollama and the GPU and are run explicitly: `venv\Scripts\python -m pytest tests/test_launcher_window.py -m e2e -s`.
See [TESTING.md](TESTING.md).

## Dashboard changes
The dashboard is built from `interface/app` (Node 20+): `cd interface/app && npm install && npm run build`. The built bundle in
`interface/app/dist` is committed, so a normal install needs no Node.
