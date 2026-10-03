#!/usr/bin/env bash
# Runs one command with its output in the log as usual. When it fails, its last lines also become an error
# annotation on the run page, which anyone can read without signing in (the full log needs a GitHub login).
#   ci/run.sh <title> <command...>
set -o pipefail
title="$1"; shift
log="$(mktemp)"
"$@" 2>&1 | tee "$log"
code=${PIPESTATUS[0]}
if [ "$code" -ne 0 ]; then
  msg="$(tail -n 12 "$log" | sed -e 's/%/%25/g' -e 's/\r//g' | awk '{printf "%s%%0A", $0}')"
  echo "::error title=${title} failed (exit ${code})::${msg}"
fi
exit "$code"
