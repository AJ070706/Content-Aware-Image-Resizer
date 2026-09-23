# ImageResizer

A local desktop image-processing application with a React/TypeScript interface, a Python desktop host, and a C++ seam-highlighting engine.

## Current capabilities

Open images, preview vertical seams in red, restore the original, change preview scale, and export PNG files. Processing stays on your computer. This version highlights seams; actual resizing, enlargement, and height adjustment are not yet supported. The original Tkinter interface remains in `src/main.py` as a legacy reference.

## Windows setup

Development requires Python 3.11, Node.js 22.12+ (or a compatible newer LTS), and Microsoft C++ Build Tools with the Desktop development with C++ workload.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
cd frontend
npm ci
cd ..
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

Each build creates a new timestamped directory under `build/`, preserving older builds. It contains the source snapshot, logs, compiled extension, bundled frontend, dependency versions, and `release/ImageResizer.exe`.

## Single-file executable

Run `release/ImageResizer.exe` from a build directory. Python, the native module, and frontend assets are bundled; a separate Python or Node installation is not required. PyInstaller extracts embedded resources to a temporary directory at startup.

The target Windows machine must have Microsoft Edge WebView2 Runtime and .NET Framework 4.6.2 or newer. The runtime is not included in this executable. This is a single application file, not a guarantee of zero operating-system prerequisites. Release distribution still needs clean-machine testing and optional code signing.

## Verification

Point `PYTHONPATH` at a build's `app` directory, then run `.\.venv\Scripts\python.exe -m unittest discover -s tests`. The packaged executable accepts `--smoke-test <absolute-result.json>` to check React and bridge startup and exit. The source snapshot and dependency log identify each build independently of later edits.
