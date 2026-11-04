#!/usr/bin/env bash
# Lumen Finance EMI agent: one entry point for setup, a live call, tests and evals.
#
#   ./run.sh setup                  install, create .env, check keys and livekit-server, run tests
#   ./run.sh call [B002] [profile]  LiveKit + browser client + agent; Ctrl-C stops all three
#   ./run.sh test                   unit and replay tests (no network, free)
#   ./run.sh evals [args]           text evals, e.g. ./run.sh evals --scenarios dev --runs 5 (paid; asks first)
#   ./run.sh judge results/<run>    LLM tone judge on a finished eval run (paid; asks first)
#   ./run.sh report results/<run>   results tables for the eval report (free)
#   ./run.sh dashboard <base> [<after>]  results/dashboard.html from eval runs (free)
#   ./run.sh label                  results/labelling.html: hand-label 20 calls for judge calibration
#   ./run.sh calibrate              compare judges with evals/labels/owner_labels.json (free tiers)
#
# Keys live in .env (gitignored). Nothing here prints a key.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
# Import the agent from src/ directly, so nothing depends on an editable install working.
export PYTHONPATH="$PWD/src:$PWD${PYTHONPATH:+:$PYTHONPATH}"
LIVEKIT_PORT=7880
CLIENT_PORT=8080
LOG_DIR=logs
REQUIRED_KEYS=(DEEPGRAM_API_KEY SARVAM_API_KEY LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET)
OPTIONAL_KEYS=(GROQ_API_KEY CARTESIA_API_KEY)

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

port_open() { (echo >"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

env_value() {  # the value of a key in .env, or empty
  [[ -f .env ]] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
}

check_keys() {
  local missing=()
  for k in "${REQUIRED_KEYS[@]}"; do [[ -n "$(env_value "$k")" ]] || missing+=("$k"); done
  if ((${#missing[@]})); then
    fail "missing in .env: ${missing[*]} (see .env.example)"
  fi
  for k in "${OPTIONAL_KEYS[@]}"; do
    [[ -n "$(env_value "$k")" ]] || say "  note: $k not set (optional: LLM fallback / Cartesia TTS)"
  done
}

cmd_setup() {
  say "1/5 Python"
  "$PY" -c 'import sys; assert sys.version_info >= (3, 10), sys.version' \
    || fail "Python 3.10 or newer is needed (set PYTHON=/path/to/python3)"
  "$PY" --version

  say "2/5 Dependencies"
  "$PY" -m pip install -q -r requirements.txt
  # Optional: run.sh sets PYTHONPATH itself. Old setuptools cannot do a pyproject editable install.
  "$PY" -m pip install -q -e . 2>/dev/null \
    || echo "  editable install skipped (old setuptools); run.sh uses PYTHONPATH=src instead"

  say "3/5 .env"
  if [[ ! -f .env ]]; then
    # The local LiveKit dev server's fixed credentials (public defaults, not secrets).
    sed -e 's|^LIVEKIT_URL=$|LIVEKIT_URL=ws://localhost:7880|' \
        -e 's|^LIVEKIT_API_KEY=$|LIVEKIT_API_KEY=devkey|' \
        -e 's|^LIVEKIT_API_SECRET=$|LIVEKIT_API_SECRET=secret|' .env.example > .env
    say "  created .env from .env.example: add DEEPGRAM_API_KEY and SARVAM_API_KEY, then run setup again"
    exit 1
  fi
  check_keys
  echo "  required keys present"

  say "4/5 livekit-server"
  command -v livekit-server >/dev/null \
    || fail "livekit-server not found. Install it: https://docs.livekit.io/home/self-hosting/local/ (macOS: brew install livekit)"
  livekit-server --version

  say "5/5 Tests"
  "$PY" -m pytest -q -p no:warnings
  say "Setup done. Start a call with: ./run.sh call B002"
}

cmd_call() {
  local borrower=${1:-B002} profile=${2:-hindi_bulbul}
  [[ -f .env ]] || fail "no .env yet: run ./run.sh setup first"
  check_keys
  command -v livekit-server >/dev/null || fail "livekit-server not found: run ./run.sh setup"
  mkdir -p "$LOG_DIR"
  local pids=()
  cleanup() {
    trap - EXIT INT TERM  # run once, not again on exit after Ctrl-C
    say "Stopping..."
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
    wait 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  if port_open "$LIVEKIT_PORT"; then
    say "LiveKit already running on :$LIVEKIT_PORT (reusing it)"
  else
    say "Starting LiveKit (log: $LOG_DIR/livekit.log)"
    livekit-server --dev >"$LOG_DIR/livekit.log" 2>&1 & pids+=($!)
    for _ in $(seq 50); do port_open "$LIVEKIT_PORT" && break; sleep 0.1; done
    port_open "$LIVEKIT_PORT" || fail "LiveKit did not start; see $LOG_DIR/livekit.log"
  fi

  if port_open "$CLIENT_PORT"; then
    say "Browser client already running on :$CLIENT_PORT (reusing it)"
  else
    say "Starting the browser client (log: $LOG_DIR/client.log)"
    "$PY" scripts/serve_client.py >"$LOG_DIR/client.log" 2>&1 & pids+=($!)
  fi

  say "Agent: borrower $borrower, profile $profile. Open http://localhost:$CLIENT_PORT and press call."
  say "Agent log is below and in $LOG_DIR/bot-$borrower.log. Ctrl-C ends everything."
  "$PY" -m emi_agent.bot --profile "$profile" --borrower "$borrower" 2>&1 | tee "$LOG_DIR/bot-$borrower.log"
}

case "${1:-help}" in
  setup)  cmd_setup ;;
  call)   shift; cmd_call "$@" ;;
  test)   "$PY" -m pytest -q -p no:warnings ;;
  evals)  shift; "$PY" -m evals.run "$@" ;;
  judge)  shift; "$PY" -m evals.judge "$@" ;;
  report) shift; "$PY" -m evals.report "$@" ;;
  dashboard) shift; "$PY" -m evals.dashboard "$@" ;;
  label)  "$PY" -m evals.label_page ;;
  calibrate) shift; "$PY" -m evals.calibrate "$@" ;;
  *)      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
