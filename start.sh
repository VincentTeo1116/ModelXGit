#!/usr/bin/env bash
# ModelXGit (Model X): one-command start on macOS / Linux.
#   ./start.sh              real Bob if .env has BOB_API_KEY, else the labelled stand-in
#   ./start.sh --stand-in   always the stand-in (no key needed)
# Then open http://127.0.0.1:8000/ (PORT=... to change it).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
STANDIN=0
[ "${1:-}" = "--stand-in" ] && STANDIN=1

[ -x .venv/bin/python ] || { echo "Creating .venv ..."; python3 -m venv .venv; }
PY=.venv/bin/python
echo "Installing requirements ..."
"$PY" -m pip install -q --disable-pip-version-check -r requirements.txt

has_key() { [ -f .env ] && grep -Eq '^[[:space:]]*BOB_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' .env \
            && ! grep -Eq '^[[:space:]]*BOB_API_KEY[[:space:]]*=[[:space:]]*your_' .env; }

MOCK=""
if [ "$STANDIN" = 1 ] || ! has_key; then
  echo "Using the Bob STAND-IN (no real Bob calls). Put BOB_API_KEY in .env to use real IBM Bob."
  export BOB_CLIENT=http BOB_API_KEY=stand-in BOB_API_ENDPOINT=http://127.0.0.1:8765 BOB_SKILLS_PATH=/inference/v1/skills/run
  "$PY" -m uvicorn devtools.mock_bob:app --port 8765 --log-level warning &
  MOCK=$!
else
  command -v bob >/dev/null || { echo "Bob Shell not found. Install it with: curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash"; exit 1; }
  echo "Using real IBM Bob through Bob Shell."
fi
trap '[ -n "$MOCK" ] && kill "$MOCK" 2>/dev/null || true' EXIT

( sleep 3; { command -v open >/dev/null && open "http://127.0.0.1:$PORT/"; } \
  || { command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:$PORT/"; } || true ) >/dev/null 2>&1 &
echo "ModelXGit on http://127.0.0.1:$PORT/  (Ctrl+C to stop)"
"$PY" -m uvicorn main:app --port "$PORT"
