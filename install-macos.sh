#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Sentinel Archive"
DESKTOP_COMMAND_NAME="Sentinel Archive.command"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${HOME}/Desktop/Sentinel-Archive.log"
PORT=9200
INSTALL_DEPS=0
REBUILD=0
NO_BROWSER=0
LAUNCH=0
PREPARE_ONLY=0

usage() {
  cat <<USAGE
Usage:
  ./install-macos.sh                 Install dependencies and create a Desktop launcher
  ./install-macos.sh --launch        Start ${APP_NAME}

Options:
  --port PORT        FastAPI/control-panel port (default: ${PORT})
  --install-deps     Reinstall Python and npm dependencies before launch
  --rebuild          Rebuild the React control panel before launch
  --no-browser       Do not open the browser automatically
  --prepare-only     Install dependencies without starting the app
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --launch) LAUNCH=1 ;;
    --install-deps) INSTALL_DEPS=1 ;;
    --rebuild) REBUILD=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    --prepare-only) PREPARE_ONLY=1 ;;
    --port)
      PORT="${2:?Missing value for --port}"
      shift
      ;;
    --port=*) PORT="${1#*=}" ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

log() {
  mkdir -p "$(dirname "$LOG_FILE")"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This installer is intended for macOS." >&2
    exit 1
  fi
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

require_node() {
  command -v node >/dev/null 2>&1 || {
    echo "Node.js 20+ is required. Install it from https://nodejs.org/ or Homebrew." >&2
    exit 1
  }
  node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)" || {
    echo "Node.js 20+ is required. Current version: $(node --version)" >&2
    exit 1
  }
  command -v npm >/dev/null 2>&1 || {
    echo "npm is required with Node.js." >&2
    exit 1
  }
}

prepare_runtime() {
  local python_bin
  python_bin="$(find_python)" || {
    echo "Python 3.11+ is required. Install it from https://www.python.org/ or Homebrew." >&2
    exit 1
  }
  require_node

  local venv_dir="${ROOT_DIR}/.venv"
  local venv_python="${venv_dir}/bin/python"
  if [[ ! -x "$venv_python" ]]; then
    log "Creating Python virtual environment"
    "$python_bin" -m venv "$venv_dir"
    INSTALL_DEPS=1
  fi

  if [[ "$INSTALL_DEPS" -eq 1 || ! -d "${venv_dir}/lib" ]]; then
    log "Installing Python dependencies"
    "$venv_python" -m pip install --upgrade pip
    "$venv_python" -m pip install -r "${ROOT_DIR}/requirements.txt"
  fi

  if [[ "$INSTALL_DEPS" -eq 1 || ! -d "${ROOT_DIR}/node_modules" ]]; then
    log "Installing frontend dependencies"
    (cd "$ROOT_DIR" && npm install)
  fi

  if [[ "$REBUILD" -eq 1 || ! -f "${ROOT_DIR}/dist/index.html" ]]; then
    log "Building control panel"
    (cd "$ROOT_DIR" && npm run build)
  fi
}

create_desktop_launcher() {
  local desktop_dir="${HOME}/Desktop"
  local command_path="${desktop_dir}/${DESKTOP_COMMAND_NAME}"
  mkdir -p "$desktop_dir"
  cat > "$command_path" <<EOF
#!/usr/bin/env bash
cd "$ROOT_DIR"
exec "$ROOT_DIR/install-macos.sh" --launch
EOF
  chmod +x "$command_path"
  log "Desktop launcher created: ${command_path}"
}

wait_url() {
  local url="$1"
  local seconds="${2:-60}"
  local start
  start="$(date +%s)"
  while (( "$(date +%s)" - start < seconds )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

launch_app() {
  prepare_runtime
  if [[ "$PREPARE_ONLY" -eq 1 ]]; then
    log "Preparation complete"
    return 0
  fi

  local venv_python="${ROOT_DIR}/.venv/bin/python"
  local url="http://127.0.0.1:${PORT}"
  log "Starting ${APP_NAME} on ${url}"
  (cd "$ROOT_DIR" && "$venv_python" -m uvicorn sentinel_archive.main:app --host 127.0.0.1 --port "$PORT") >> "$LOG_FILE" 2>&1 &
  local server_pid=$!

  cleanup() {
    kill "$server_pid" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT INT TERM

  if ! wait_url "${url}/api/health" 75; then
    log "Startup failed. Recent log output:"
    tail -n 80 "$LOG_FILE" || true
    exit 1
  fi

  log "Ready: ${url}"
  if [[ "$NO_BROWSER" -eq 0 ]]; then
    open "$url"
  fi
  wait "$server_pid"
}

require_macos
if [[ "$LAUNCH" -eq 1 ]]; then
  launch_app
else
  INSTALL_DEPS=1
  PREPARE_ONLY=1
  prepare_runtime
  create_desktop_launcher
  log "Install complete. Double-click '${DESKTOP_COMMAND_NAME}' on the Desktop to start ${APP_NAME}."
fi
