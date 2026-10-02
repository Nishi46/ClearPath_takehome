# Dependency audit

## Current (Step 1, 2026-10-01)

Python 3.11.8. `pip-audit -r requirements-dev.txt` reports **no known vulnerabilities**. `pip check` is clean, and the runtime pins install in a fresh venv.

| Package | Pin | Kind |
|---|---|---|
| fastapi | 0.142.2 | runtime |
| uvicorn | 0.54.0 | runtime |
| jinja2 | 3.1.6 | runtime |
| python-multipart | 0.0.32 | runtime |
| openpyxl | 3.1.5 | runtime (Excel import; pulls in et-xmlfile 2.0.0) |
| pytest | 9.1.1 | dev |
| httpx | 0.28.1 | dev |

Transitive dependencies (starlette 1.7.0, anyio, click and others) are left unpinned and resolve to patched versions. Pinning the full `pip freeze` is optional hardening for later.

## History

The first pass ran on the system Python 3.9.6. `pip-audit` reported 15 vulnerabilities (starlette, python-multipart, anyio, click, pytest), and every fix needed Python 3.10 or later. Decision: move to Python 3.11.8 and re-pin rather than accept the risk. Re-run `pip-audit` before deploy (Step 15) and set `PYTHON_VERSION=3.11.8` on Render.

## Excel import (phase 8)

Added openpyxl 3.1.5 for reading `.xlsx` files. Run `pip-audit -r requirements-dev.txt` and confirm a clean result before deploy; it has not been re-run since this pin was added. Files are read with `read_only=True, data_only=True` (no formulas evaluated, no macros), after a zip check that refuses `.xlsm`, oversize unzipped content and non-workbook archives, because openpyxl's own docs say it is not protected against maliciously constructed XML.
