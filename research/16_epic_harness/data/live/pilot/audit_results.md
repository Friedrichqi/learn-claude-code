# Grader audit (G7), batch 1: 15 pilot runs, judged blind to arm

Sheet: `audit_view.md` (`epic_analyze.py audit --live live/pilot --n 15 --seed epic16-audit-pilot`, 2026-10-09
06:20). Each run was judged against its template's task text before reading the arm key. "Agree" means the
human pass/fail equals the grader's.

| # | cell (template) | human | grader | agree | note |
|---|---|---|---|---|---|
| 1 | packaging-E1 (E1) | fail | fail (0.0) | yes | 20/22 modules described, no file:line anywhere; the task asks for a citation per module |
| 2 | rich-E2 (E2) | fail | fail (0.0) | yes | call cap, no answer |
| 3 | rich-E3 (E3) | fail | fail (0.0) | yes | files named, no line numbers |
| 4 | jinja2-M1 (M1) | fail | fail (0.0) | yes | 5 of 24 files tagged; one insertion split `reduce` (`from functools import re` … `'profiling'duce`): broken import |
| 5 | yaml-M2-load (M2) | fail | fail (0.25) | yes | definition renamed, 3 call sites not; call cap |
| 6 | yaml-M3-constructor (M3) | pass | pass (1.0) | yes | 50/50 one-line docstrings; comments turned into docstrings; behaviour unchanged |
| 7 | rich-E1 (E1) | fail | fail (0.0) | yes | call cap, no answer |
| 8 | rich-E2 (E2) | fail | fail (0.0) | yes | call cap, no answer |
| 9 | rich-E3 (E3) | fail | fail (0.33) | yes | one valid line citation (entry point); data structures and errors without lines |
| 10 | jinja2-M1 (M1) | fail | fail (0.0) | yes | same broken insertion as #4 (identical up to it: same prompts and seeds) |
| 11 | yaml-M2-load (M2) | fail | fail (0.0) | yes | definition renamed but moved, call sites not renamed, dumper/loader rewritten (MRO import error) |
| 12 | packaging-M3-version (M3) | fail | fail (0.0) | yes | file rewritten without the missing docstrings; two existing docstrings changed |
| 13 | rich-E1 (E1) | fail | fail (0.0) | yes | call cap, no answer |
| 14 | s15_harness-E2 (E2) | fail | fail (0.0) | yes | call cap (re-reads; `agent_loop` lives in the 150K-character `code.py`) |
| 15 | yaml-M2-load (M2) | fail | fail (0.0) | yes | renamed twice (`load_v2_v2`), call sites not renamed |

**Agreement 15/15.** Observations for the rubric (no grader change):

- Scores are strict products (E1 recall × validity, E3 classes × validity, M1/M2/M3 zero on collateral
  change), so an answer that omits line numbers scores 0 even with complete content (#1, #3). That matches
  the task text, which asks for a file and line per item; the content components stay in `details`.
- Ranges such as "lines 1-100" count as a citation of line 1 (E1, valid when the line exists). Accepted as
  written: the task asks for "a file and line", and the rule is the same for every arm.
- Separately checked (not in this sample): M3 0.0 for a rewrite that does not parse (`yaml` prefix,
  IndentationError), M3 0.0 for a rewrite without the docstrings (`packaging` prefix), M2 0.0 for
  `load_v2_v2` (prefix_aa): all correct.
