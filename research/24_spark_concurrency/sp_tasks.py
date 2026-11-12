#!/usr/bin/env python3
"""Topic 24's task pool: 8 SWE-bench Lite tasks that are not in SWE-bench Verified, and 8 SWE-bench Verified tasks,
each with a native arm64 environment image.

  fetch    the two test splits from the Hugging Face datasets-server (instance_id, repo, base_commit, version,
           problem_statement) -> data/sources/{lite,verified}.json
  draw     seeded shuffle (SEED) of Lite minus Verified and of Verified; walk each list and keep the first tasks whose
           image ghcr.io/epoch-research/swe-bench.eval.arm64.<id> exists, at most MAX_PER_REPO per repository: 8 main and
           RESERVES reserves per set. The main list alternates Lite, Verified, Lite, ... -> data/tasks.json
  build    Harbor task folders data/taskdirs/<id>/{task.toml, instruction.md, environment/} (the environment folder
           stays empty: Harbor uploads any file in it into the container) and the queue data/queue/main/q<NN>__<id>
           (Harbor loads a folder in sorted order; with -k K it runs K passes over the 16 tasks)
  sanity   needs Docker: pull each main image and check it inside a container (aarch64, /testbed at base_commit, the
           package imports with the test environment's Python); a failing task is replaced by its set's next reserve
  status   print the pool

    python3 research/24_spark_concurrency/sp_tasks.py fetch
    python3 research/24_spark_concurrency/sp_tasks.py draw && python3 research/24_spark_concurrency/sp_tasks.py build
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

SEED = 2424
PER_SET = 8
RESERVES = 4
MAX_PER_REPO = 2
SOURCES = C.DATA / "sources"
SETS = {"lite": "princeton-nlp/SWE-bench_Lite", "verified": "princeton-nlp/SWE-bench_Verified"}
SIZES = {"lite": 300, "verified": 500}
FIELDS = ("instance_id", "repo", "base_commit", "version", "problem_statement", "created_at")
IMAGE = "ghcr.io/epoch-research/swe-bench.eval.arm64.{id}:latest"
IMPORTS = {"django/django": "django", "sympy/sympy": "sympy", "pytest-dev/pytest": "pytest",
           "sphinx-doc/sphinx": "sphinx", "psf/requests": "requests", "pylint-dev/pylint": "pylint",
           "mwaskom/seaborn": "seaborn", "pallets/flask": "flask", "astropy/astropy": "astropy",
           "scikit-learn/scikit-learn": "sklearn", "matplotlib/matplotlib": "matplotlib", "pydata/xarray": "xarray"}
TESTBED_PATH = "/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def image(iid: str) -> str:
    return IMAGE.format(id=iid.lower())


# -- fetch --------------------------------------------------------------------------------------------------------------
def _rows(ds: str, n: int) -> list[dict]:
    out = []
    for off in range(0, n, 100):
        url = (f"https://datasets-server.huggingface.co/rows?dataset={ds}&config=default&split=test"
               f"&offset={off}&length=100")
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=120) as r:
                    rows = json.load(r)["rows"]
                break
            except Exception as e:                              # noqa: BLE001
                print(f"[fetch] {ds} {off}: retry {attempt + 1} ({e!r:.80})", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            raise SystemExit(f"[fetch] failed: {ds} offset {off}")
        out += [{k: x["row"].get(k) for k in FIELDS} for x in rows]
    return out


def fetch() -> int:
    SOURCES.mkdir(parents=True, exist_ok=True)
    for name, ds in SETS.items():
        rows = _rows(ds, SIZES[name])
        (SOURCES / f"{name}.json").write_text(json.dumps(rows))
        print(f"[fetch] {name}: {len(rows)} rows")
    return 0


# -- draw ---------------------------------------------------------------------------------------------------------------
def image_exists(iid: str) -> bool | None:
    repo = f"epoch-research/swe-bench.eval.arm64.{iid.lower()}"
    try:
        with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull", timeout=30) as r:
            tok = json.load(r)["token"]
        req = urllib.request.Request(f"https://ghcr.io/v2/{repo}/manifests/latest", method="HEAD", headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403, 404) else None
    except Exception:                                           # noqa: BLE001
        return None


def draw() -> int:
    lite = json.loads((SOURCES / "lite.json").read_text())
    ver = json.loads((SOURCES / "verified.json").read_text())
    vids = {r["instance_id"] for r in ver}
    pools = {"lite": [r for r in lite if r["instance_id"] not in vids], "verified": ver}
    rng = random.Random(SEED)
    picked, checked = {}, []
    for name in ("lite", "verified"):
        rows = sorted(pools[name], key=lambda r: r["instance_id"])
        rng.shuffle(rows)
        keep, per_repo = [], {}
        for r in rows:
            if len(keep) >= PER_SET + RESERVES:
                break
            if per_repo.get(r["repo"], 0) >= MAX_PER_REPO:
                continue
            ok = image_exists(r["instance_id"])
            checked.append({"set": name, "id": r["instance_id"], "arm64": ok})
            if ok:
                keep.append(r)
                per_repo[r["repo"]] = per_repo.get(r["repo"], 0) + 1
        picked[name] = keep
        print(f"[draw] {name}: pool {len(rows)}, kept {len(keep)} after checking {sum(c['set'] == name for c in checked)}")
    main = [x for pair in zip(picked["lite"][:PER_SET], picked["verified"][:PER_SET]) for x in pair]
    data = {"seed": SEED, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rule": f"seeded shuffle; first {PER_SET} per set with an arm64 image, at most {MAX_PER_REPO} per repo; "
                    "alternating lite, verified",
            "pools": {k: len(v) for k, v in pools.items()},
            "main": [entry(r, "lite" if r in picked["lite"] else "verified") for r in main],
            "reserves": {k: [entry(r, k) for r in v[PER_SET:]] for k, v in picked.items()},
            "image_checks": checked, "replaced": [], "sanity": {}}
    save(data)
    for i, t in enumerate(data["main"], 1):
        print(f"  q{i:02d} {t['set']:8s} {t['id']:40s} {t['repo']}")
    return 0


def entry(r: dict, name: str) -> dict:
    return {"id": r["instance_id"], "set": name, "repo": r["repo"], "base_commit": r["base_commit"],
            "version": r["version"], "image": image(r["instance_id"]), "problem_statement": r["problem_statement"]}


def load() -> dict:
    return json.loads(C.TASKS_JSON.read_text())


def save(data: dict) -> None:
    C.TASKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    C.TASKS_JSON.write_text(json.dumps(data, indent=1) + "\n")


# -- build --------------------------------------------------------------------------------------------------------------
TASK_TOML = """# topic 24 (research/24_spark_concurrency/sp_tasks.py build): {set} task, verifier off in every run
schema_version = "1.4"

[metadata]
instance_id = "{id}"
dataset = "{dataset}"
repo = "{repo}"
base_commit = "{base_commit}"

[environment]
docker_image = "{image}"
"""


def build() -> int:
    data = load()
    if C.TASKDIRS.exists():
        shutil.rmtree(C.TASKDIRS)
    for t in data["main"] + [x for v in data["reserves"].values() for x in v]:
        d = C.TASKDIRS / t["id"]
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "task.toml").write_text(TASK_TOML.format(dataset={"lite": "SWE-bench_Lite", "verified":
                                                               "SWE-bench_Verified"}[t["set"]], **t))
        (d / "instruction.md").write_text(t["problem_statement"].rstrip() + "\n")
    q = C.QUEUE / "main"
    if q.exists():
        shutil.rmtree(q)
    q.mkdir(parents=True)
    for i, t in enumerate(data["main"], 1):
        (q / f"q{i:02d}__{t['id']}").symlink_to(Path("..") / ".." / "taskdirs" / t["id"])
    print(f"[build] {len(data['main'])} queued in {q}; task folders in {C.TASKDIRS}")
    return 0


def queue_names() -> list[str]:
    return sorted(p.name for p in (C.QUEUE / "main").iterdir())


# -- sanity (needs Docker) ----------------------------------------------------------------------------------------------
def sanity_one(t: dict, pull: bool = True) -> dict:
    res = {"id": t["id"], "image": t["image"]}
    if pull:
        p = subprocess.run(["docker", "pull", "-q", t["image"]], capture_output=True, text=True, timeout=1800)
        res["pull_rc"] = p.returncode
        if p.returncode:
            res["error"] = p.stderr.strip()[-400:]
            return res
    mod = IMPORTS.get(t["repo"], t["repo"].split("/")[-1])
    script = (f"uname -m; cd /testbed && git rev-parse HEAD && git status --porcelain | wc -l; "
              f"python -c 'import {mod}, sys; print(sys.version.split()[0], {mod}.__file__)'; "
              f"python -m pytest --version 2>&1 | head -1")
    p = subprocess.run(["docker", "run", "--rm", "--network", "none", "-e", f"PATH={TESTBED_PATH}", t["image"],
                        "bash", "-c", script], capture_output=True, text=True, timeout=300)
    lines = p.stdout.strip().splitlines()
    res.update(rc=p.returncode, out=lines[:6], err=p.stderr.strip()[-300:])
    res["ok"] = (p.returncode == 0 and len(lines) >= 4 and lines[0] == "aarch64" and lines[1] == t["base_commit"]
                 and "/testbed" in lines[3])
    return res


def sanity() -> int:
    data = load()
    for k, t in enumerate(list(data["main"])):
        while True:
            r = sanity_one(t)
            data["sanity"][t["id"]] = r
            print(f"[sanity] {t['id']}: {'ok' if r.get('ok') else 'FAIL'} {r.get('out')} {r.get('error', '')}",
                  flush=True)
            if r.get("ok"):
                break
            res = data["reserves"][t["set"]]
            if not res:
                print(f"[sanity] no reserve left for {t['set']}")
                save(data)
                return 1
            new = res.pop(0)
            data["replaced"].append({"old": t["id"], "new": new["id"], "why": r.get("error") or r.get("out")})
            data["main"][k] = new
            t = new
        save(data)
    print("[sanity] all main tasks ok; run `build` again if anything was replaced")
    return 0


def status() -> int:
    data = load()
    for i, t in enumerate(data["main"], 1):
        s = data.get("sanity", {}).get(t["id"], {})
        print(f"q{i:02d} {t['set']:8s} {t['id']:40s} {t['repo']:28s} sanity={s.get('ok')}")
    print(f"replaced: {data.get('replaced')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("fetch", "draw", "build", "sanity", "status"))
    a = ap.parse_args()
    return {"fetch": fetch, "draw": draw, "build": build, "sanity": sanity, "status": status}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
