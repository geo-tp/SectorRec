#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# pytest discovers both unittest classes and the function-based regressions.
# Use xvfb-run -a ./run-tests.sh on Linux without a graphical display.
exec "${PYTHON:-python3}" -m pytest "$@"
