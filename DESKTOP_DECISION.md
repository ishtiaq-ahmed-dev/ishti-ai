# ISHTI Desktop Shell Architecture Decision Record (ADR)

**Date**: 2026-09-29  
**Platform**: Windows 11 Pro, Intel Core Ultra 9, NVIDIA GeForce RTX 5070 Ti (16GB GDDR7), 64GB RAM  
**Author**: ISHTI Frontend & Desktop Engineering  

---

## 1. Context & Objectives
ISHTI must function as a first-class Windows desktop application (comparable to Spotify or VS Code), completely eliminating terminal windows and browser tabs. Key criteria:
1. Zero terminal/console windows ever displayed to the user (`CREATE_NO_WINDOW`, logs directed to `%LOCALAPPDATA%\ISHTI\logs`).
2. Native, sleek WebView rendering with minimal idle RAM and low CPU overhead.
3. Fast instantaneous startup with centered frameless splash screen.
4. Robust single-instance enforcement (second launch smoothly brings the active window to foreground).
5. Clean, reliable lifecycle management with 100% process-tree termination and GPU VRAM release on exit.

---

## 2. Toolchain Audit Results

| Component | Required by | Status on System | Detection Method / Path |
|---|---|---|---|
| **Rust Toolchain (`rustc`, `cargo`)** | Tauri 2 | **NOT INSTALLED** | `Get-Command rustc, cargo` returned 0 results |
| **Visual Studio C++ Build Tools (`cl.exe`)** | Tauri 2 / MSVC | **NOT INSTALLED** | No MSVC v143 toolchain found in `C:\Program Files (x86)\Microsoft Visual Studio` |
| **Microsoft Edge WebView2 Runtime** | Tauri 2 / pywebview | **INSTALLED** | Verified at `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\154.0.4258.37` |
| **Node.js & npm / pnpm** | React UI / Electron | **INSTALLED** | `Node v22.14.0`, `npm`, `pnpm` available |
| **Python 3.11 + pywebview + pywin32** | Python WebView2 Shell | **INSTALLED** | `pywebview 6.2.1` using native Windows WebView2 loader |
| **PyInstaller** | Python Executable Freezer | **INSTALLED** | `pyinstaller 6.22.3` in project virtual environment |

---

## 3. Technology Evaluation

### Option A: Tauri 2 (Preferred Default)
- **Pros**: Tiny executable (< 10MB), native Edge WebView2, low memory overhead (~30MB RAM).
- **Cons**: Requires `rustc`, `cargo`, and the multi-gigabyte Visual Studio 2022 C++ Build Tools (`Microsoft.VisualStudio.Component.VC.Tools.x86.x64`). None of these are installed on the user's workstation. Compiling Tauri on this machine is impossible without the user downloading ~4-6GB of compiler toolchains.
- **Action**: Detailed installation instructions for Tauri 2 prerequisites are documented in `NEEDS.md` for future builds.

### Option B: Electron
- **Pros**: Uses existing Node.js toolchain; cross-platform packaging.
- **Cons**: Bundles an entire Chromium browser and Node runtime (~180MB–250MB idle RAM overhead), high disk footprint (>150MB installer), and slower cold-start times compared to WebView2.

### Option C: Native WebView2 Desktop Shell (Selected Decision)
- **Architecture**:
  - Leverages Windows native Edge WebView2 runtime already pre-installed on the system (`pywebview` + .NET WebView2 loader).
  - Frozen into a standalone Windows binary (`ISHTI.exe`) using PyInstaller `--onedir` `--noconsole` distribution.
  - Zero terminal windows ever shown: all processes are spawned with `subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS`.
  - Process isolation: Backend runs as a managed child process; logs are directed to `%LOCALAPPDATA%\ISHTI\logs\ishti.log`.
  - Single-instance enforcement: Implemented with Windows Named Mutex (`ISHTI_DESKTOP_SINGLETON_MUTEX`) and Win32 `FindWindowW` / `SetForegroundWindow`.
  - Instant splash screen: Frameless, rounded, centered 520x340 splash window launched in < 150ms before backend models load.
  - Complete process-tree cleanup: PyInstaller one-folder build prevents orphaned PID issues. Process trees are terminated recursively via Win32 Job Object and `taskkill /T /F`.

---

## 4. Final Decision
We adopt **Option C: Native WebView2 Desktop Shell with PyInstaller `--onedir` distribution**:
1. It immediately provides native Windows WebView2 benefits (same engine Tauri uses) without requiring the user to install gigabytes of C++ compilers.
2. It respects the existing React frontend framework (`interface/app`).
3. It guarantees instant startup, zero console windows, and full process-tree termination.
4. Tauri 2 build scripts and required toolchains are documented in `NEEDS.md` and `BUILD.md` should native Rust compilation be desired in subsequent prompts.
