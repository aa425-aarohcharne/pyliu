# Pyliu

Pyliu is a lightweight Python assembly experimentation framework for building, compiling, and executing simple assembly-like programs. It combines a small CPU instruction DSL, a compiler layer, and multiple runtime backends so you can explore low-level execution patterns from Python.

## Features

- A simple CPU instruction abstraction layer
- A compiler pipeline for assembling programs into machine code
- An in-process JIT-style runtime
- A bare-metal QEMU runtime for isolated execution
- An optional CLI for installing QEMU explicitly

## Installation

Install from the project directory:

```bash
pip install pyliu .
```

Or, if you are using the local virtual environment:

```bash
.venv\Scripts\python.exe -m pip install pyliu.
```

## Optional QEMU dependency

QEMU is treated as an optional dependency for Pyliu.

By default, Pyliu does not try to download or install anything automatically. If you want to use the QEMU-backed runtime, install it explicitly:

```bash
python -m Pyliu install-qemu
```

Or, if you are using the project virtual environment:

```bash
.venv\Scripts\python.exe -m Pyliu install-qemu
```

## Quick start

```python
from Pyliu.verification import Program

program = Program()
program.begin()
program.mov("eax", "1")
program.ret()
program.end()

print(program.compile())
```

## Using the QEMU runtime

```python
from Pyliu.verification import Program, QEMURuntime

program = Program()
program.begin()
program.mov("eax", "1")
program.ret()
program.end()

runtime = QEMURuntime(timeout=5)
print(runtime.run(program))
```

## CLI

Pyliu exposes a small CLI for optional QEMU installation:

```bash
python -m Pyliu --help
python -m Pyliu install-qemu
```

## Project structure

```text
Pyliu/
├── __init__.py
├── __main__.py
├── verification.py
└── tests/
    └── test_qemu_optional.py
```

## Notes

- The QEMU runtime is intended for experimentation and research-oriented workflows.
- The package is designed to remain safe and non-invasive for PyPI-style packaging by avoiding automatic system modifications during import or normal runtime use.

## License

This project is provided as-is for educational and research use.
