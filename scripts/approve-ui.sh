#!/bin/sh
# --cwd keeps relative --unit and --workspace paths anchored where pinesky typed them, after the cd below.
here=$(pwd)
cd "$(dirname "$0")/.." || exit 1
exec python3 skill_eval.py approve-ui --cwd "$here" "$@"
