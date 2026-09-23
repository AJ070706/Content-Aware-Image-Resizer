# ImageResizer

A local desktop image-processing application with a React/TypeScript interface, a Python desktop host, and a C++ seam-highlighting engine.

## Current capabilities

Open images, preview vertical and horizontal seams in red, restore the original, change preview scale, and export PNG files. Set target width and height to see all seams needed to shrink an image to that size. Preview mode does not resize the image; Save exports the original-size seam preview. Enlargement is unsupported. The original Tkinter interface remains in `src/main.py` as a legacy reference.

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

## Native engine contract

`main.modify(image, new_width, new_height)` shrinks width first, then height by removing one minimum-energy seam at a time. Energy is the sum of absolute RGB differences between clamped left/right and up/down neighbors. Ties choose the leftmost bottom endpoint, then the leftmost predecessor while backtracking. Horizontal seams use the same rule on the transposed image.

The desktop preview starts independent vertical and horizontal seam-order workers after an image is loaded. They publish completed seam ranks incrementally. A preview waits only for its requested seam prefix; background computation continues afterward. Width and height adjustment are exclusive because each cached seam order is calculated independently from the original image.

`main.highlight` runs the same carving sequence but marks every removed pixel red at its original position and returns the original dimensions. Both functions accept nonempty NumPy uint8 RGB/RGBA arrays, including strided or read-only views; they return independent contiguous arrays and preserve alpha. Targets must be integers within the original dimensions. Invalid shapes, types, empty images, and enlargement requests raise exceptions. Native computation releases the Python GIL after copying the input.

Native tests use exhaustive path enumeration on small images to verify optimal seams, exact output pixels, original-coordinate highlighting, all valid small target sizes, alpha, input validation, and array layouts.
