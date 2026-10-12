# Grader audit (G7), batch 2: 15 screen runs

Sheet: `audit_view.md` (`epic_analyze.py audit --live live/screen --n 15 --seed epic16-audit-screen`, 2026-10-09
14:40 UTC). The screen ran only the `prefix` arm, so this batch checks the graders, not arm blinding. Each run was
judged against its template's task text and the frozen pass rules (§3.3). "Agree" means the human pass/fail
equals the grader's.

| # | cell (template) | human | grader at audit | agree | note |
|---|---|---|---|---|---|
| 1 | requests-E1 (E1) | pass | fail (0.5) | **no** | 19/19 modules, each "- `x.py`: description. (Line N)" with an existing line; the grader found no citation in that layout |
| 2 | httpx-E2 (E2) | fail | fail (0.0) | yes | call cap, no answer |
| 3 | typer-E3 (E3) | fail | fail (0.67) | yes | entry points and data structures cited; error handling named without a line |
| 4 | jinja2-M1 (M1) | fail | fail (0.11) | yes | 5 of 24 files tagged; changelog lines mostly wrong |
| 5 | rich-M2-pick_unit_and_suffix (M2) | fail | fail (0.0) | yes | definition renamed, call sites not; module rewritten (docstrings dropped, format string broken) |
| 6 | s15_harness-M3-scripts_trace_workflow_viz (M3) | fail | fail (0.0) | yes | file truncated from 891 to 4 lines |
| 7 | typer-E1 (E1) | pass | fail (0.5) | **no** | 32/32 modules; each item's "**File/Line**: `symbol` (line N)" sits on the line after the item's file name; lines exist (mostly a uniform "line 10") |
| 8 | jinja2-E2 (E2) | fail | fail (0.0) | yes | call cap, no answer |
| 9 | tqdm-E3 (E3) | fail | fail (0.0) | yes | the three cited lines are imports, not an entry point, a data structure or error handling |
| 10 | rich-M1 (M1) | fail | fail (0.0) | yes | nothing tagged, empty answer |
| 11 | packaging-M2-create_compatible_tags_selector (M2) | fail | fail (0.0) | yes | only `__all__` changed, to `..._v2_v2`; definition and call sites not renamed |
| 12 | tqdm-M3-notebook (M3) | fail | fail (0.7) | yes | 7 of 10 functions documented |
| 13 | jinja2-E1 (E1) | fail | fail (0.0) | yes | two-line summary, no module named |
| 14 | requests-E2 (E2) | fail | fail (0.0) | yes | call cap, no answer |
| 15 | rich-E3 (E3) | fail | fail (0.0) | yes | call cap, no answer |

**Agreement 13/15 at audit time; both misses are the same grader defect, now fixed.** The citation extractor
(`epic_grade.extract_citations`) took a line number only next to its path. It now also takes a parenthesized
"(line N)" anywhere on a line that names exactly one path, and a "(line N)" or "Line Number: N" on the following
lines of the same block (until a blank line or a line naming another path); `test_citation_block_formats`
covers both layouts and the non-attribution cases. Re-grading pilot and screen changed 4 screen runs and no
pilot run: #1 and #7 now pass (agreeing with the human verdicts), `yaml-E1` (same layout as #1, not sampled)
passes, and #9 moves from 0.0 to 0.33 (still a fail, as judged). **After the fix, 15/15 in this batch and 30/30
over both batches (G7 ≥ 95 %: met).**

Scope: only EXPLAIN templates (E1–E3) use citation extraction. The main sweep has only MODIFY cells (M1–M3),
whose graders agreed 13/13 over both batches and did not change.

Rubric note, unchanged: E1 citation validity means the cited line exists in the cited file, so a uniform
"line 13" for every module (`yaml-E1`) or "line 10" (#7) counts as valid. The rule is lenient and applies
equally to every arm.
