# ImageResizer

An in-progress desktop seam-carving project with a Python/Tkinter interface and a C++ backend exposed with pybind11. The current GUI calls the seam-highlighting operation.

## Local setup (Windows / PowerShell)

Install Python with Tkinter and Microsoft C++ Build Tools with the Desktop development with C++ workload. Existing compiled artifacts were built for Python 3.11; rebuild for the Python environment you use.

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install numpy Pillow pybind11 setuptools
Push-Location src
..\.venv\Scripts\python.exe setup.py build_ext --inplace
..\.venv\Scripts\python.exe main.py
Pop-Location
```

These commands describe the existing build layout; setup has not yet been validated on a fresh environment. Local images and generated build outputs are excluded from Git.
