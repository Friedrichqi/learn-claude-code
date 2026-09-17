# Inventory: `normalize_key` occurrences in `profiling_sandbox/refactor/`

Inventory of every appearance of the identifier `normalize_key`.

## Method / coverage

`grep -rn` could not be executed in this (asynchronous) turn — the shell tool was
blocked. The inventory was instead produced by reading **every** file under
`profiling_sandbox/refactor/`. A recursive glob of
`profiling_sandbox/refactor/**` shows exactly four files:

```
profiling_sandbox/refactor/pkg.py
profiling_sandbox/refactor/report.py
profiling_sandbox/refactor/store.py
profiling_sandbox/refactor/test_pkg.py
```

All four were read in full, so the listing below is exhaustive for the directory.

## Summary

- **Total occurrences:** 7
- **definition:** 1
- **import:** 2
- **call:** 4
- **other (attribute/string/comment):** 0

## Exhaustive listing (file:line — classification — matched line)

| # | file:line | classification | matched line text |
|---|-----------|----------------|-------------------|
| 1 | `profiling_sandbox/refactor/pkg.py:4` | definition | `def normalize_key(raw):` |
| 2 | `profiling_sandbox/refactor/report.py:3` | import | `from pkg import normalize_key` |
| 3 | `profiling_sandbox/refactor/report.py:7` | call | `    return "# " + normalize_key(title)` |
| 4 | `profiling_sandbox/refactor/report.py:13` | call | `        lines.append("%s=%s" % (normalize_key(key), store.get(key)))` |
| 5 | `profiling_sandbox/refactor/store.py:3` | import | `from pkg import normalize_key` |
| 6 | `profiling_sandbox/refactor/store.py:11` | call | `        self._items[normalize_key(key)] = value` |
| 7 | `profiling_sandbox/refactor/store.py:14` | call | `        return self._items.get(normalize_key(key))` |

## By classification

### definition
- `profiling_sandbox/refactor/pkg.py:4` — `def normalize_key(raw):`

### import
- `profiling_sandbox/refactor/report.py:3` — `from pkg import normalize_key`
- `profiling_sandbox/refactor/store.py:3` — `from pkg import normalize_key`

### call
- `profiling_sandbox/refactor/report.py:7` — `    return "# " + normalize_key(title)`
- `profiling_sandbox/refactor/report.py:13` — `        lines.append("%s=%s" % (normalize_key(key), store.get(key)))`
- `profiling_sandbox/refactor/store.py:11` — `        self._items[normalize_key(key)] = value`
- `profiling_sandbox/refactor/store.py:14` — `        return self._items.get(normalize_key(key))`

### other
- none

## Notes for the rename task

- `normalize_key` is defined once in `pkg.py` (line 4) and imported by name in
  `report.py` (line 3) and `store.py` (line 3) via `from pkg import normalize_key`.
- All four call sites are bare-name calls (no attribute access, e.g. no `pkg.normalize_key`).
- `profiling_sandbox/refactor/test_pkg.py` contains **no** occurrences of `normalize_key`;
  it already refers to `canonical_key` (`from pkg import canonical_key` and two call sites).
