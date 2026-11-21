#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ] || [ ! -f web/dist/index.html ]; then
  echo 'Előbb futtasd a README telepítési és build parancsait.' >&2
  exit 1
fi
exec .venv/bin/python -m autotuner.app
