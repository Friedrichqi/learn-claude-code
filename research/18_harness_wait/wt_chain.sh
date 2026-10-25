#!/usr/bin/env bash
# Run rounds of research/18_harness_wait cells back to back, stopping at the first failed round.
#   research/18_harness_wait/wt_chain.sh "p08_none:A p16_none:B" "p16_m_stock:A p16_m_share:B" ...
# Each argument is one round (two cells, one per GPU pair, run by wt_pipeline.py round).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY=/data/fritz/venv/bin/python
for round in "$@"; do
  echo "[chain] $(date -u +%FT%TZ) round: $round"
  # shellcheck disable=SC2086
  $PY "$DIR/wt_pipeline.py" round $round
  rc=$?
  echo "[chain] $(date -u +%FT%TZ) round exit $rc"
  $PY "$DIR/wt_pipeline.py" hours | tail -1
  if [ $rc -ne 0 ]; then
    echo "[chain] stopping: a round failed"
    exit $rc
  fi
done
echo "[chain] all rounds done"
