#!/usr/bin/env bash
# Run the backend test suite inside a network namespace that only has loopback:
# an OS-level barrier (also covering native subprocesses) on top of the in-process
# guard of webapp/backend/tests/netguard.py. No test may reach a printer.
#
#   scripts/run_tests_offline.sh [pytest args…]       e.g.  -q -rs -p no:cacheprovider
#
# Order of attempts: unprivileged user+net namespace (Linux dev machines), then
# `sudo unshare --net` (CI runners). If neither works:
#   - FREEGLAZ_REQUIRE_NETNS=1 (set in CI): fail with a diagnostic, never run unisolated;
#   - otherwise: run with the in-process guard alone, with a warning (local convenience).
# Inside the namespace, the interface list is checked: anything but `lo` aborts the run.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
CMD=("$PY" -m pytest webapp/backend/tests "$@")
uid="$(id -u)"; gid="$(id -g)"
# shellcheck disable=SC2016
CHECK_LO='ifs=$(ip -o link show | awk -F": " "{print \$2}" | sed "s/@.*//" | sort -u | tr "\n" " "); [ "$ifs" = "lo " ] || { echo "run_tests_offline: namespace has interfaces other than lo: $ifs" >&2; exit 97; }'

if unshare -rn true 2>/dev/null; then
  echo "run_tests_offline: unprivileged network namespace (loopback only)" >&2
  # nested user namespace: back to the real uid, so permission-based tests behave as usual
  exec unshare -rn bash -c "ip link set lo up && $CHECK_LO"' && exec unshare --user --map-user="$1" --map-group="$2" -- "${@:3}"' \
    _ "$uid" "$gid" "${CMD[@]}"
elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
  # sudo resets the environment (HOME=/root, FREEGLAZ_* dropped) and sudo-rs, the default
  # on Ubuntu 26.04 runners, ignores a bare --preserve-env. Pass the caller's exported
  # variables as arguments and restore exactly them with `env -i` once back to the real
  # uid, so the suite sees the same environment as on the unprivileged path.
  CALLER_ENV=()
  while IFS= read -r name; do CALLER_ENV+=("$name=${!name}"); done < <(compgen -e)
  echo "run_tests_offline: sudo network namespace (loopback only), caller environment kept (HOME=$HOME)" >&2
  exec sudo -n unshare --net -- bash -c \
    "ip link set lo up && $CHECK_LO"' && n="$3" && exec setpriv --reuid="$1" --regid="$2" --init-groups -- env -i "${@:4:n}" "${@:$((4 + n))}"' \
    _ "$uid" "$gid" "${#CALLER_ENV[@]}" "${CALLER_ENV[@]}" "${CMD[@]}"
elif [ "${FREEGLAZ_REQUIRE_NETNS:-0}" = "1" ]; then
  {
    echo "run_tests_offline: ERROR — no network namespace could be created, and FREEGLAZ_REQUIRE_NETNS=1."
    echo "  unshare -rn: $(unshare -rn true 2>&1 || true)"
    echo "  sudo -n:     $( (sudo -n true) 2>&1 || true)"
    echo "  kernel.unprivileged_userns_clone=$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo n/a)"
    echo "  kernel.apparmor_restrict_unprivileged_userns=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo n/a)"
    echo "  Refusing to run the suite with the in-process guard alone."
  } >&2
  exit 96
else
  echo "run_tests_offline: WARNING no network namespace available — in-process guard only" >&2
  exec "${CMD[@]}"
fi
