# Content-Aware Image Resizer

A Windows desktop app that resizes images by removing or inserting low-cost seams. A React/TypeScript interface talks to a Python desktop host and a C++ seam-carving engine. Processing stays on your computer.

## Use the app

1. Open a PNG, JPEG, WebP, BMP, or TIFF image. Vertical and horizontal seam orders begin calculating in separate background workers.
2. Choose **Highlight seams** to mark removed pixels red, or **Modify image** to preview the resized result.
3. Choose **Classic** (backward energy) or **Forward energy** seam quality. Forward energy considers the new edges created by removing a seam. Switching quality restarts both background seam calculations while keeping the selected target.
4. Optionally choose a **Protect**, **Remove**, or **Erase** brush and paint on the original image. Green makes marked pixels expensive for seams to cross; pink favors their removal. Each changed stroke restarts both background seam calculations. **Clear guidance** removes all marks.
5. Choose **Width** or **Height**, then set a target with the number field or slider. You can shrink to one pixel or enlarge to twice the original size in the selected direction. The preview moves toward that target as seams become available. Only one dimension can be adjusted at a time.
6. Select **Energy map** above the canvas to inspect the first seam's cumulative path costs. Blue means lower cost within a row (width) or column (height), orange higher cost, and red the selected seam. Choose a different direction or quality to inspect that calculation. Turn the map off to resume resizing.
7. In Modify mode, use **Compare with original** and its slider to reveal the original and resized previews side by side within the same image frame. Each keeps its natural aspect ratio.
8. Save a PNG. Highlight mode saves an original-size image with removal or insertion paths marked; Modify mode saves the resized image. **Restore original** returns the selected dimension to its starting size.

The **Info** tab in the app explains the same algorithm and controls. Simultaneous width-and-height adjustment is not supported in the desktop interface.

## How controls interact

| Change | What stays | What changes |
| --- | --- | --- |
| Highlight ↔ Modify | Direction, target, quality, and guidance | Preview and save output switch between red seam marks and resized pixels. Brushes are usable only on the original image in Highlight; **Edit guidance in Highlight mode** returns there without losing the target. |
| Width ↔ Height | Image, quality, and guidance; both seam workers keep calculating | The new direction starts at its original size. An unfinished preview in the previous direction is superseded. |
| Classic ↔ Forward | Direction, target, mode, and guidance | Both seam orders restart under the chosen scoring method; the preview catches up as paths become available. |
| Change or clear guidance | Direction, target, mode, and quality | Both seam orders restart, and the selected preview is rebuilt. Existing guidance also affects Modify mode even though painting is disabled there. |
| Energy map on/off | Mode, target, comparison setting, brush selection, and guidance | The map temporarily replaces the image preview. Editing and saving pause until the map is closed. |
| Open another image | Seam-quality selection | Return to the Workspace in Highlight at the new image's original width and fit-to-window zoom; clear guidance, comparison, and the energy map. |

Saving waits until the latest requested preview is ready. Changing controls while a preview is drawing replaces that preview; an older result cannot become the selected saved image.

## How seam carving works here

A vertical seam contains one pixel in every row. Consecutive seam pixels may stay in the same column or move one column left or right. A horizontal seam follows the equivalent path across columns. Define `D(a,b)` as the sum of absolute differences of the RGB channels of two pixels; alpha is preserved in output but does not influence seam choice. At image edges, missing neighbors are replaced with the nearest edge pixel.

**Classic (backward) energy** scores a pixel at `(x,y)` as `D(left,right) + D(up,down)`. It measures contrast already present around the pixel, then chooses the connected path with the lowest sum. It is simple and often favors flat areas, but it does not explicitly account for an edge formed when pixels on either side of a removed seam become neighbors.

**Forward energy** starts with `C_U = D(left,right)`, the cost of joining the left and right neighbors. A path arriving from the upper-left adds `D(up,left)`; arriving from the upper-right adds `D(up,right)`; arriving straight down adds no extra transition cost. The dynamic program compares these entry-dependent totals. This can avoid some newly created edge artifacts, but it cannot guarantee a more natural result for every image. Both modes add a large cost to green protection marks and subtract a cost from pink removal marks.

Dynamic programming finds the connected seam with the lowest total energy. A painted protection pixel adds a large cost, while a removal pixel subtracts a cost in either quality mode; these are strong preferences, not absolute guarantees. It stores the cheapest cost to reach each pixel from the previous row, chooses the cheapest bottom endpoint, then backtracks. Equal costs choose the leftmost endpoint or predecessor. The selected seam is removed, and energy is recalculated on the smaller image before the next seam. Horizontal seams use the same algorithm on a transposed image.

After an image loads, the desktop host starts **independent** width and height calculations from the original image for the selected quality mode. Changing quality or brush guidance cancels those orders and starts new ones. Each worker publishes completed seam positions in original-image coordinates. The UI fetches those positions in small batches. Highlight mode paints or erases red marks. For shrinking, Modify mode compacts the surviving pixels. Moving the slider backward reverses the preview from the cached order. Save renders the selected seam prefix in C++, so the exported pixels match the preview.

The **Energy map** view runs the same C++ first-seam calculation and displays the cumulative minimum path cost at every pixel, including Forward entry costs and brush penalties. The first winning path is marked red. Colors are normalized independently for each row when adjusting width, or each column when adjusting height. This makes within-row or within-column comparisons visible despite cumulative costs growing along the path. It is a diagnostic of the **first** seam on the original image, not a prediction of all future seams: each later removal changes the image and requires a new score calculation. The view does not change the saved image.

For **enlargement**, the engine finds seams by repeatedly removing them from a *temporary copy* while recording their coordinates in the original. This gives distinct insertion paths. It then leaves the original pixels in place and inserts one new pixel beside each selected path pixel. Each new pixel averages the source pixel with its adjacent original neighbor, including alpha; at the outer edge the neighbor is taken from the other side. If the selected dimension is only one pixel, no distinct neighbor exists and that pixel is duplicated. For height, the same process runs on a transposed image. Because a single pass inserts at most one pixel beside each original pixel, enlargement is limited to 2×. Blending avoids simple exact duplication in ordinary cases, but it cannot reconstruct missing content.

This is local color-difference energy with optional user-painted guidance, not subject recognition. Forward energy can reduce some newly created edge artifacts but does not guarantee a visually better result in every image. A seam may still cross protected detail when no suitable path avoids it. A single insertion pass supports up to 2× enlargement in either dimension; a one-pixel dimension has no distinct neighboring pixel to blend. The native `main.modify` function can resize both dimensions in one call, width first, but the desktop UI deliberately selects only one because its two cached seam orders were computed independently.

## Project layout

| Path | Purpose |
| --- | --- |
| `frontend/src/` | React controls, live canvas preview, and in-app algorithm guide |
| `src/desktop.py` | pywebview file dialogs, image state, worker threads, and save API |
| `src/main.cpp` | C++ Classic/Forward seam search, energy-map analysis, cached orders, highlighting, and resizing |
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

`main.modify(image, new_width, new_height, energy='backward')` returns an image resized by seam removal or insertion. `main.highlight(...)` follows the same seam sequence but marks removal or insertion paths red in the original-size image. `main.analyze_energy(image, direction='vertical', mask=None, energy='backward')` returns an `int64` cumulative-cost array with the same height and width as the input, plus the first seam's flattened original-image positions. These functions accept `energy='forward'` to select the new criterion. They accept nonempty NumPy `uint8` RGB/RGBA arrays, including strided or read-only views, and return independent contiguous arrays while preserving alpha. Targets must be integers from 1 through twice the original dimension. Invalid arrays, zero-sized targets, and larger targets raise errors. The engine copies input before releasing the Python GIL for the search.

Tests compare the native seams with exhaustive path enumeration on small images, check every valid small target size, and verify cached-prefix rendering, input validation, alpha, array layouts, and concurrent calls.
