#!/usr/bin/env bash
# Run the backend test suite inside a network namespace that only has loopback:
# an OS-level barrier (also covering native subprocesses) on top of the in-process
# guard of webapp/backend/tests/netguard.py. No test may reach a printer.
#
#   scripts/run_tests_offline.sh [pytest args…]       e.g.  -q -rs -p no:cacheprovider
#
# Order of attempts: unprivileged user+net namespace (Linux dev machines), then
# `sudo unshare --net` (CI runners), else the in-process guard alone (with a warning).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
CMD=("$PY" -m pytest webapp/backend/tests "$@")
uid="$(id -u)"; gid="$(id -g)"

if unshare -rn true 2>/dev/null; then
  echo "run_tests_offline: unprivileged network namespace (loopback only)" >&2
  # nested user namespace: back to the real uid, so permission-based tests behave as usual
  exec unshare -rn bash -c 'ip link set lo up && exec unshare --user --map-user="$1" --map-group="$2" -- "${@:3}"' \
    _ "$uid" "$gid" "${CMD[@]}"
elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
  echo "run_tests_offline: sudo network namespace (loopback only)" >&2
  exec sudo -n --preserve-env unshare --net -- bash -c \
    'ip link set lo up && exec setpriv --reuid="$1" --regid="$2" --init-groups -- "${@:3}"' \
    _ "$uid" "$gid" "${CMD[@]}"
else
  echo "run_tests_offline: WARNING no network namespace available — in-process guard only" >&2
  exec "${CMD[@]}"
fi
