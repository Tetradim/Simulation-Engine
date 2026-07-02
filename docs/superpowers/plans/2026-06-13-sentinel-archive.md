# Sentinel Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone market replay and Edge/Pulse-compatible Sentinel Archive with a web control panel.

**Architecture:** FastAPI owns the deterministic simulation state and serves Edge/Pulse-compatible REST contracts. React/Vite provides an operator panel for importing bars, adjusting assumptions, controlling playback, sending handoffs, and inspecting simulated state.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, React 19, Vite, TypeScript.

---

## File Structure

- `sentinel_archive/models.py`: Pydantic models for bars, config, positions, handoffs, and snapshots.
- `sentinel_archive/core.py`: Replay clock, execution model, account state, idempotent handoff processing, and event log.
- `sentinel_archive/csv_import.py`: CSV parser for user-supplied OHLCV market-day data.
- `sentinel_archive/contracts.py`: Edge/Pulse handoff schema document.
- `sentinel_archive/api.py`: FastAPI routes for native simulation control plus Edge/Pulse compatibility.
- `sentinel_archive/main.py`: Uvicorn entry point.
- `tests/`: Behavioral tests written before implementation.
- `frontend/src/`: React control panel.
- `README.md`: Setup, launch, API, Sentinel Core integration, replay data format.

## Tasks

- [x] Create spec, plan, manifests, and backend tests.
- [ ] Implement the simulation models, CSV parser, core replay/execution engine, and contract API.
- [ ] Add React/Vite control panel for configuration, CSV import, replay controls, handoff testing, and state inspection.
- [ ] Add Windows launcher and README usage docs.
- [ ] Run backend tests, frontend build, API smoke checks, commit, and push.
