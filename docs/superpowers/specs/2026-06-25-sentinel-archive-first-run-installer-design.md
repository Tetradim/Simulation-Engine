# Sentinel Archive first-run installer design

Date: 2026-06-25

## Goal

Windows beta testers should install Sentinel Archive from `SentinelArchive-Setup-<version>.exe`, double-click the installed shortcut, and have missing runtime dependencies handled automatically on first launch.

## Design

- Keep the existing source launcher for development.
- Add an installed-package branch to `Launch-Sentinel-Archive.ps1` when `SentinelArchive.exe` exists beside the launcher.
- The installed launcher checks/downloads the Microsoft Visual C++ Runtime, starts the packaged FastAPI app, waits for `/api/health`, verifies the bundled control panel, and opens the local dashboard.
- The Windows workflow builds the Vite control panel, packages the Python backend with PyInstaller, copies `dist/` beside the executable, and creates `SentinelArchive-Setup-<version>.exe` with Inno Setup.

## Non-goals

- No live broker integration; Sentinel Archive remains a local testing and recorder tool.
- No Node.js runtime in the installed app; the built frontend is static.
- No macOS installer redesign.
