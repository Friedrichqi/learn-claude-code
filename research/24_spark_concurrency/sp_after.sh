#!/bin/bash
# Topic 24: wait until the chain has finished and analysed a cell, then print where things stand. It only waits and
# reads: the analysis itself is run by sp_chain.py between cells (an analysis that overlaps the next server's start
# takes the memory vLLM and umbench need, see README, Changes, 2026-11-11).
#   sp_after.sh <cell> [<cell> ...]
cd "$(dirname "$0")" || exit 1
for cell in "$@"; do
  # the cell's own record decides (chain.log may still hold the lines of an earlier, failed try of the same cell)
  state() { grep -o '"status": "[a-z]*"' data/runs/$cell/cell.json 2>/dev/null | head -1 | cut -d'"' -f4; }
  until [ "$(state)" = failed ] || { [ "$(state)" = done ] && grep -q "cell $cell: analysed" data/runs/chain.log; } \
        || ! pgrep -f "sp_[c]hain.py run" >/dev/null; do sleep 30; done
  grep "cell $cell: status\|cell $cell: analysed\|cell $cell: repeating" data/runs/chain.log | tail -3 | cut -c1-200
done
$HOME/.venv/bin/python sp_chain.py status
sed -n '/## Data-quality/,$p' data/tables/sp.md | grep -v "yes |$"
date
