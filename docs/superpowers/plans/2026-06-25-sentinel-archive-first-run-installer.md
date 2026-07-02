# Sentinel Archive First-Run Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installed Windows launcher and setup artifact that repair missing runtime dependencies on first launch.

**Architecture:** Source checkouts continue through `.venv`, npm, and Vite. Installed packages are detected by `SentinelArchive.exe`; that path repairs VC++ runtime, starts the packaged FastAPI backend, serves the copied built control panel from `dist/`, and opens the dashboard.

**Tech Stack:** PowerShell, FastAPI, Vite, PyInstaller, Inno Setup, unittest static checks.

---

### Task 1: Static tests

**Files:**
- Create: `tests/test_windows_installer_bootstrap_static.py`

- [ ] Add tests covering installed/source launcher detection, VC++ runtime repair, packaged entrypoint, workflow packaging, and README instructions.
- [ ] Run `python -m unittest discover -s tests -p "test_windows_installer_bootstrap_static.py" -v` and confirm it fails before implementation.

### Task 2: Packaged entrypoint and launcher

**Files:**
- Create: `windows_entrypoint.py`
- Modify: `Launch-Sentinel-Archive.bat`
- Modify: `Launch-Sentinel-Archive.ps1`

- [ ] Add a packaged uvicorn entrypoint controlled by `HOST` and `PORT`.
- [ ] Harden the batch wrapper for partial extracts and argument forwarding.
- [ ] Add installed launcher mode with VC++ runtime repair and `/api/health` wait.

### Task 3: Workflow and docs

**Files:**
- Create: `.github/workflows/build.yml`
- Modify: `README.md`

- [ ] Build the Vite control panel.
- [ ] Package `SentinelArchive.exe`, copied `dist/`, and launcher pair.
- [ ] Build/upload `SentinelArchive-Setup-<version>.exe`.
- [ ] Document beta installer behavior and support logs.
