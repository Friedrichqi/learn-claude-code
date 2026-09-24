# Inventory: `normalize_key` in profiling_sandbox/refactor/

Scope: every definition, import, and call of the exact identifier `normalize_key`.
Paths are relative to /home/yq335/lanes/census. Matches use exact identifier
boundaries (word boundaries).

## Occurrences

- profiling_sandbox/refactor/pkg.py:4 — DEFINITION — `def normalize_key(raw):`
- profiling_sandbox/refactor/report.py:3 — IMPORT — `from pkg import normalize_key`
- profiling_sandbox/refactor/report.py:7 — CALL — `return "# " + normalize_key(title)`
- profiling_sandbox/refactor/report.py:13 — CALL — `lines.append("%s=%s" % (normalize_key(key), store.get(key)))`
- profiling_sandbox/refactor/store.py:3 — IMPORT — `from pkg import normalize_key`
- profiling_sandbox/refactor/store.py:11 — CALL — `self._items[normalize_key(key)] = value`
- profiling_sandbox/refactor/store.py:14 — CALL — `return self._items.get(normalize_key(key))`

## Notes

- No near-matches (e.g., `normalize_keyboard`) exist anywhere in profiling_sandbox/refactor/ — every `normalize_key` substring hit is the exact identifier listed above.
- profiling_sandbox/refactor/test_pkg.py contains zero `normalize_key` occurrences; it already imports and calls `canonical_key` (`from pkg import canonical_key` on line 12), so the rename should make the module code match the existing tests.

Total: 7 occurrences (1 definition, 2 imports, 4 calls).
