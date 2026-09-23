# setup.py
from pybind11.setup_helpers import Pybind11Extension, build_ext
import pybind11
from setuptools import setup, Extension

ext_modules = [
    Pybind11Extension(
        "main",
        ["main.cpp"],
        include_dirs=[
            pybind11.get_include(),
        ],
        language='c++'
    ),
]

setup(
    name="main",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
    zip_safe=False,
    python_requires=">=3.6",
)