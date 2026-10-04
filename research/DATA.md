# Research data lives on Google Drive

Since 2026-10-03, the experiment results in `research/<topic>/data/` are not in git. Raw traces, sidecars, sandboxes,
logs, `scrape.jsonl` files and sampler dumps are mirrored file by file on Google Drive, in the rclone remote
`gdrive:` (folder `learn-claude-code-data/` in the Drive of the account that set it up):

```
gdrive:learn-claude-code-data/
  research/<topic>/data/…        the mirror (symlinks are stored as *.rclonelink files)
  _replaced/<UTC>/research/…     older versions of files that a later push replaced
  git-history/learn-claude-code-full-history-2026-10-03.bundle    the history before the rewrite
```

## What git still tracks under `data/`

`.gitignore` ignores `research/*/data/**` except:

- **Tables and figures.** These are the `*.md` tables and `*.svg` figures outside agent sandboxes (`*.sandbox/`), which the READMEs link to.
- **Frozen inputs that scripts and tests read:**
  - `04_kv_splice/data/kv_splice/run_*.requests.jsonl`, read by `10_rope_shift/test_rope_shift.py`.
  - `10_rope_shift/data/rope_shift_live/manifest{.jsonl,_meta.json}`, the workload manifest.
  - `11_multiuser_kv/data/multiuser_live/sessions{.jsonl,_meta.json}`, read by `mu_workloads.py` and `12_hw_utilization/test_hw.py`.
  - `12_hw_utilization/data/{sessions.jsonl,conditions.json}`.
  - `02_input_redundancy/data/input_redundancy_runs.md`, read by `05_latency_breakdown/provider_probe.py`.

A new topic needs no change. If a new script reads a file from `data/` as input, add a `!` line for it in
`.gitignore`.

## Syncing

```
python3 research/common/drive_data.py status            # local vs Drive by MD5 (--quick: sizes only)
python3 research/common/drive_data.py push 12           # after a run: upload new and changed files
python3 research/common/drive_data.py pull 11 12        # fresh clone: fetch topics' data (no TOPIC = all)
```

- `push` never deletes anything on Drive.
- `pull` only adds missing files, so local results that aren't pushed yet are never overwritten. Pass `--overwrite` to replace local files that differ.
- Topics are folder names or their number prefix.
- Drive accepts about 3 new files per second, so a topic with 10k+ files takes about an hour. Run big pushes with `nohup` on the login node.

## Setup on a new machine

1. Install rclone, for example the static binary from <https://rclone.org/downloads/> into `~/.local/bin`.
2. Run `rclone config create gdrive drive scope=drive.file` and sign in with the **same** Google account.
3. On a headless node, first open a tunnel from your laptop with `ssh -L 53682:127.0.0.1:53682 <node>`, then open the printed `http://127.0.0.1:53682/auth?…` link in your laptop browser.

With the `drive.file` scope, rclone sees only the files that rclone itself uploaded to that account. That is
enough here and keeps the rest of the Drive out of reach.

That "rclone itself" means rclone's shared client ID, the default OAuth client that uploaded the mirror on 2026-10-03.
rclone is retiring it during 2026. When it stops working:

1. Make your own client ID: <https://rclone.org/drive/#making-your-own-client-id>.
2. Re-create the remote with `client_id=… client_secret=… scope=drive`.

A new client with `drive.file` scope cannot see the files the old client uploaded. The files stay visible in the Drive web UI either
way.

## The 2026-10-03 history rewrite

- **What changed:** the results were removed from every commit with `git filter-repo`. Only the 17 commits after `develop` were rewritten. `develop` and the upstream commits keep their SHAs.
- **Old SHAs:** commit SHAs cited in READMEs, decks or notes from before that date are the old ones. [`history_commit_map.tsv`](history_commit_map.tsv) maps each old SHA to its new one.
- **The old history:** the bundle on Drive holds every old ref, including the raw data. Restore it with `git clone learn-claude-code-full-history-2026-10-03.bundle old-history`.
