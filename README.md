# Content-Aware Image Resizer

A Windows desktop app that resizes images by removing low-energy seams. A React/TypeScript interface talks to a Python desktop host and a C++ seam-carving engine. Processing stays on your computer.

## Use the app

1. Open a PNG, JPEG, WebP, BMP, or TIFF image. Vertical and horizontal seam orders begin calculating in separate background workers.
2. Choose **Highlight seams** to mark removed pixels red, or **Modify image** to preview the resized result.
3. Choose **Classic** (backward energy) or **Forward energy** seam quality. Forward energy considers the new edges created by removing a seam. Switching quality restarts both background seam calculations while keeping the selected target.
4. Optionally choose a **Protect**, **Remove**, or **Erase** brush and paint on the original image. Green makes marked pixels expensive for seams to cross; pink favors their removal. Each changed stroke restarts both background seam calculations. **Clear guidance** removes all marks.
5. Choose **Width** or **Height**, then set a target with the number field or slider. The preview moves toward that target as seams become available. Only one dimension can be adjusted at a time.
6. In Modify mode, use **Compare with original** and its slider to reveal the original and resized previews side by side within the same image frame. Each keeps its natural aspect ratio.
7. Save a PNG. Highlight mode saves an original-size marked image; Modify mode saves the smaller image. **Restore original** returns the selected dimension to its starting size.

The **Info** tab in the app explains the same algorithm and controls. Enlargement and simultaneous width-and-height adjustment are not supported.

## How seam carving works here

A vertical seam contains one pixel in every row. Consecutive seam pixels may stay in the same column or move one column left or right. A horizontal seam follows the equivalent path across columns. **Classic** uses backward energy: for each pixel, it sums absolute RGB differences between its left and right neighbors and between its upper and lower neighbors. **Forward energy** instead scores the new horizontal edge after removal, plus an additional edge cost when the seam steps diagonally. At image edges, missing neighbors are replaced with the nearest edge pixel.

Dynamic programming finds the connected seam with the lowest total energy. A painted protection pixel adds a large cost, while a removal pixel subtracts a cost in either quality mode; these are strong preferences, not absolute guarantees. It stores the cheapest cost to reach each pixel from the previous row, chooses the cheapest bottom endpoint, then backtracks. Equal costs choose the leftmost endpoint or predecessor. The selected seam is removed, and energy is recalculated on the smaller image before the next seam. Horizontal seams use the same algorithm on a transposed image.

After an image loads, the desktop host starts **independent** width and height calculations from the original image for the selected quality mode. Changing quality or brush guidance cancels those orders and starts new ones. Each worker publishes completed seam positions in original-image coordinates. The UI fetches those positions in small batches. Highlight mode paints or erases red marks; Modify mode compacts the surviving pixels into a new canvas. Moving the slider backward restores pixels from the cached order. Save renders the selected seam prefix in C++, so the exported pixels match the preview.

This is local color-difference energy with optional user-painted guidance, not subject recognition. Forward energy can reduce some newly created edge artifacts but does not guarantee a visually better result in every image. A seam may still cross protected detail when no suitable path avoids it. The native `main.modify` function can shrink both dimensions in one call, width first, but the desktop UI deliberately selects only one because its two cached seam orders were computed independently.

## Project layout

| Path | Purpose |
| --- | --- |
| `frontend/src/` | React controls, live canvas preview, and in-app algorithm guide |
| `src/desktop.py` | pywebview file dialogs, image state, worker threads, and save API |
| `src/main.cpp` | C++ backward-energy seam search, cached orders, highlighting, and resizing |
| `src/setup.py` | pybind11 extension build |
| `scripts/build.ps1` | timestamped build and single-file Windows executable |
| `scripts/prune-builds.ps1` | retain one verified build after its commit is pushed |
| `tests/` | native correctness and desktop bridge tests |
| `legacy/` | unused earlier Tkinter interface |

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

Each build creates a new timestamped directory under `build/` with a source snapshot, logs, compiled extension, frontend bundle, dependency versions, and `release/ImageResizer.exe`. Builds remain separate during development. After a build passes tests and the packaged startup check and its source commit is pushed, run `.\scripts\prune-builds.ps1 -KeepBuild .\build\<timestamp>` to keep only that verified build. Use `-WhatIf` to preview cleanup.

`build/` is ignored by Git: GitHub commits save source code, not the generated executables or old build logs. Copy an executable elsewhere if it must be retained before pruning.

## Single-file executable

Run `release/ImageResizer.exe` from a build directory. Python, the native module, and frontend assets are bundled; a separate Python or Node installation is not required. PyInstaller extracts embedded resources to a temporary directory at startup.

The target Windows machine must have Microsoft Edge WebView2 Runtime and .NET Framework 4.6.2 or newer. The runtime is not included in this executable. This is a single application file, not a guarantee of zero operating-system prerequisites. Release distribution still needs clean-machine testing and optional code signing.

## Verification

Point `PYTHONPATH` at a build's `app` directory, then run `.\.venv\Scripts\python.exe -m unittest discover -s tests`. The packaged executable accepts `--smoke-test <absolute-result.json>` to check React and bridge startup and exit. A local GUI check can verify both tabs, live preview, and saved pixels. The source snapshot and dependency log identify each build independently of later edits.

## Native engine contract

`main.modify(image, new_width, new_height, energy='backward')` returns a smaller image. `main.highlight(...)` follows the same seam sequence but marks removed pixels red in the original-size image. Both accept `energy='forward'` to select the new criterion. They accept nonempty NumPy `uint8` RGB/RGBA arrays, including strided or read-only views, and return independent contiguous arrays while preserving alpha. Targets must be integers from 1 through the original dimension. Invalid arrays, zero-sized targets, and enlargement raise errors. The engine copies input before releasing the Python GIL for the search.

Tests compare the native seams with exhaustive path enumeration on small images, check every valid small target size, and verify cached-prefix rendering, input validation, alpha, array layouts, and concurrent calls.
