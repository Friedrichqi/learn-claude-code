"""CPU tests for research/10_rope_shift (no GPU, no vLLM import).

    python3 -m pytest research/10_rope_shift/test_rope_shift.py -q   (or run the file directly)

The RoPE re-rotation and the connector are covered on GPU by `arms.py selftest` (T1-T5); the
renderer by `render.py check` against the server's usage.input_tokens on a live run.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import analyze  # noqa: E402
import arms  # noqa: E402
import events as ev  # noqa: E402
import latency  # noqa: E402
import repair  # noqa: E402
import score  # noqa: E402
import workloads  # noqa: E402
from render import open_jsonl  # noqa: E402

KV04 = Path(__file__).resolve().parents[1] / "04_kv_splice" / "data" / "kv_splice"


def _check_plan(old, new, segs):
    pos = 0
    for seg in segs:
        assert seg[1] == pos, "segments must tile new in order"
        if seg[0] == "reuse":
            j1, j2, i1 = seg[1], seg[2], seg[3]
            assert new[j1:j2] == old[i1:i1 + (j2 - j1)]
            assert j2 - j1 >= arms.MIN_RUN
        pos = seg[2]
    assert pos == len(new)
    assert segs[-1][0] == "fresh", "the last token must be computed"


def test_splice_plan_tiles_new_exactly():
    rng = random.Random(1)
    for _ in range(50):
        a = [rng.randrange(1000) for _ in range(rng.randrange(50, 400))]
        b = [rng.randrange(1000) for _ in range(rng.randrange(0, 80))]
        c = [rng.randrange(1000) for _ in range(rng.randrange(50, 400))]
        p = [rng.randrange(1000, 1100) for _ in range(rng.randrange(1, 30))]
        tail = [rng.randrange(1100, 1200) for _ in range(rng.randrange(1, 20))]
        old, new = a + b + c, a + p + c + tail
        segs = arms.splice_plan(old, new)
        _check_plan(old, new, segs)
        stats = arms.plan_stats(old, new, segs)
        assert stats["reused"] + stats["fresh"] == len(new)
        assert stats["prefix_len"] >= min(len(a), 1) or len(a) < arms.MIN_RUN


def test_splice_plan_identity_keeps_last_token_fresh():
    seq = list(range(500))
    segs = arms.splice_plan(seq, seq)
    _check_plan(seq, seq, segs)
    assert segs[-1] == ("fresh", 499, 500)


def test_load_segments_rotate_only_for_shift():
    old = list(range(300))
    new = old[:100] + [5000, 5001] + old[150:300] + [6000]
    segs = arms.splice_plan(old, new)
    n = arms.plan_stats(old, new, segs)["tail_start"]
    shift = arms.load_segments("e", "shift", segs, n)
    norope = arms.load_segments("e", "norope", segs, n)
    assert [s[5] for s in shift if s[0] == "e/old"] == [True, True]
    assert not any(s[5] for s in norope)
    moved = [s for s in shift if s[0] == "e/old" and s[4] != 0]
    assert moved and moved[0][4] == 102 - 150


def test_events_on_04_harness_dumps():
    kinds, appended_only = set(), 0
    for path in sorted(KV04.glob("run_*.requests.jsonl")):
        recs = open_jsonl(path)
        calls = ev.lead_calls(recs)
        for i in range(1, len(calls)):
            info = ev.classify(calls[i - 1], calls[i])
            if info is None:
                appended_only += 1
                continue
            kinds.update(info["kinds"])
            assert 0 <= info["first_edit_msg"] <= info["n_msgs_new"]
            oracle = ev.oracle_messages(calls[i - 1], calls[i], info["appended_from"])
            assert oracle[: info["n_msgs_old"]] == ev.history_after(calls[i - 1])
    assert {"placeholder", "snip", "summary"} <= kinds


def test_anchored_markers():
    assert ev.is_placeholder("[Earlier tool result saved at /x/.task_outputs/tool-results/a.txt]")
    assert not ev.is_placeholder("see [Earlier tool result saved at /x] in the docs")
    assert ev.is_archive({"role": "user", "content": "[12 messages archived at /r/.transcripts/t.jsonl]"})
    assert not ev.is_archive({"role": "user", "content": "text [12 messages archived at /r/t.jsonl]"})
    assert ev.is_summary({"role": "user", "content": "[Compacted]\n\nAuthoritative request:\ngo"})


def test_gold_probes_from_a_survivor():
    text = "import os\n\n\ndef load(path):\n    return open(path).read()\n\n\ndef save(path, data):\n" \
           "    with open(path, 'w') as f:\n        f.write(data)\n    return load(path)\n\n\nclass Store:\n    pass\n"
    surv = {"msg": 3, "block": 0, "path": "profiling_sandbox/x/io.py", "text": text * 1,
            "defs": ["load", "save"], "classes": ["Store"]}
    rng = random.Random(0)
    explain = ev.gold_probe("explain", surv, rng)
    assert explain["gold"]["names"] == ["load", "save", "Store"]
    tool = ev.gold_probe("tool_call", surv, random.Random(0))
    args = tool["gold"]["arguments"]
    assert args["old_text"] in text and args["new_text"].count("_v2(") == 1


def test_parse_calls_formats():
    hermes = 'I will read it.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "./a/b.py"}}\n</tool_call>'
    calls, bad, rest = score.parse_calls(hermes, "hermes")
    assert calls == [("read_file", {"path": "./a/b.py"})] and not bad and rest == "I will read it."
    glm = "<tool_call>edit_file<arg_key>path</arg_key><arg_value>a.py</arg_value>" \
          "<arg_key>old_text</arg_key><arg_value>def f(x):</arg_value></tool_call>"
    calls, bad, _ = score.parse_calls(glm, "glm47")
    assert calls == [("edit_file", {"path": "a.py", "old_text": "def f(x):"})] and not bad
    xml = "<tool_call>\n<function=glob>\n<parameter=pattern>\n**/*.py\n</parameter>\n</function>\n</tool_call>"
    calls, bad, _ = score.parse_calls(xml, "hermes")
    assert calls == [("glob", {"pattern": "**/*.py"})]
    calls, bad, _ = score.parse_calls("<tool_call>\n{\"name\": \"read_file\", \"arguments\": {\n</tool_call>", "hermes")
    assert bad and not calls


def test_truncated_call_is_not_malformed():
    cut = '<tool_call>\n{"name": "write_file", "arguments": {"path": "a.py", "content": "BUILD_TAG = 1\n'
    assert score.partial_call_name(cut, "hermes") == "write_file"
    glm = "<tool_call>write_file<arg_key>path</arg_key><arg_value>a.py"
    assert score.partial_call_name(glm, "glm47") == "write_file"
    fake = type("S", (), {"records": {1: {"messages": [], "tools": []}}, "file_at": lambda self, p, c: None,
                          "local": lambda self, p: None, "seed": Path("/nonexistent")})()
    rec = {"text": cut, "finish": "length", "same_as_recompute": True}
    m = score.score_natural(rec, dict(rec), {"cur_call": 1}, fake, "hermes")
    assert m["kind"] == "truncated_call" and m["name_match"] and not m["malformed"] and m["truncated"]


def test_ast_canon_and_f1():
    a = score.canon(("read_file", {"path": "./a/b.py", "limit": 50.0}))
    b = score.canon(("read_file", {"limit": 50, "path": "a/b.py"}))
    assert a == b
    assert score.f1(["x", "y"], ["x", "y"]) == 1.0 and score.f1(["x"], ["y"]) == 0.0
    assert score.f1([], []) is None


def test_retention_categories():
    probe = {"family": "retention", "gold": {"answer": "ClickException"}}
    state_free = {"cur_call": 0}
    fake = type("S", (), {"records": {0: {"messages": []}}})()
    got = score.score_probe({"text": "ClickException"}, probe, fake, state_free, "hermes")
    assert got["answered"] and got["pass"]
    got = score.score_probe({"text": "That file is not available in the current context."}, probe, fake, state_free, "hermes")
    assert got["abstained"] and not got["hallucinated"]
    got = score.score_probe({"text": '<tool_call>\n{"name": "read_file", "arguments": {"path": "x.py"}}\n</tool_call>'},
                            probe, fake, state_free, "hermes")
    assert got["refetch"] and not got["hallucinated"]
    got = score.score_probe({"text": "UsageError"}, probe, fake, state_free, "hermes")
    assert got["hallucinated"]


def test_rouge_and_blocks():
    assert score.rouge_l("the cat sat", "the cat sat") == 1.0
    assert 0 < score.rouge_l("the cat sat on it", "a cat sat") < 1
    m = score.BLOCK_RE.search("<<<<<<< SEARCH\ndef f(x):\n=======\ndef f_v2(x):\n>>>>>>> REPLACE")
    assert m.group(1) == "def f(x):" and m.group(2) == "def f_v2(x):"


def test_stats_helpers():
    p, lo, hi = analyze.wilson(50, 100)
    assert abs(p - 0.5) < 1e-9 and lo < 0.5 < hi
    assert analyze.mcnemar(0, 0) == 1.0 and analyze.mcnemar(0, 10) < 0.01
    m, lo, hi = analyze.boot_mean([1, 2, 3, 4], ["a", "a", "b", "b"])
    assert lo <= m <= hi


def test_manifest_is_balanced_and_08_texts_load():
    src = workloads.items_08()
    assert len(src["EXPLAIN_ITEMS"]) == 4 and len(src["MODIFY_ITEMS"]) == 4
    assert src["MODIFY_ITEMS"][0].startswith("Add a module-level constant named BUILD_TAG")
    rows = workloads.build(500)
    assert len(rows) == len({r["label"] for r in rows}) == 500
    workloads.check(rows)



def test_repair_override_plan_partitions_the_reused_region():
    rng = random.Random(3)
    old = [rng.randrange(50) for _ in range(400)]
    new = old[:100] + [99] * 7 + old[130:260] + [98] * 5 + old[270:390] + [97] * 3
    segs = arms.splice_plan(old, new)
    t = {"segs": segs, "stats": arms.plan_stats(old, new, segs)}
    lo, hi = repair.region(t)
    assert lo == 100 and hi == len(new) - 3
    new_pos, _, _ = repair.reuse_triplets(segs, lo, hi)
    dev = [rng.random() for _ in new_pos]
    chosen = {"shift": set(), "blend05": repair.select_blend(new_pos, dev, 0.05),
              "blend15": repair.select_blend(new_pos, dev, 0.15)}
    chosen["rand15"] = repair.select_rand(new_pos, len(chosen["blend15"]), "e")
    assert len(chosen["blend15"]) == len(chosen["rand15"]) == -(-len(new_pos) * 15 // 100)
    for arm in repair.ARMS:
        plan = repair.plan_arm(t, arm, chosen)
        covered = set()
        for a, b, src, delta in plan["segs"]:
            assert old[src:src + b - a] == new[a:b] and a - src == delta
            covered.update(range(a, b))
        assert covered <= set(new_pos)
        assert len(set(new_pos) - covered) == plan["stats"]["selected"]
    heads = repair.run_heads(segs, 32, lo, hi)
    assert heads == set(range(107, 139)) | set(range(242, 274))
    assert repair.spans({1, 2, 3, 7, 9, 10}) == 3


def test_repair_unit_groups_respect_the_budget():
    chunk = [{"eid": e, "plans": {(arm, "natural"): {"lo": 0, "hi": 100} for arm in repair.ARMS}} for e in "ab"]
    units = [(arm, e) for arm in repair.ARMS for e in "ab"]
    assert repair.unit_groups(chunk, 1, 10**9) == [units]
    assert repair.unit_groups(chunk, 1, 250) == [units[i:i + 2] for i in range(0, 12, 2)]
    assert repair.unit_groups(chunk, 1, 50) == [[u] for u in units]      # an oversized unit runs alone
    spec = repair.final_spec("e", "e/shift/natural", {"lo": 10, "hi": 30})
    assert spec == {"load": {"n": 30, "segs": [["e/old", 0, 10, 0, 0, False], ["e/shift/natural", 10, 30, 10, 0, False]]}}
    stage = repair.stage_spec("e", "k", {"lo": 0, "hi": 30, "segs": []}, 1)
    assert "load" not in stage and stage["override"]["from_layer"] == 1


def test_latency_requests_load_only_reusable_rows():
    rng = random.Random(5)
    old = [rng.randrange(50) for _ in range(300)]
    new = old[:80] + [99] * 6 + old[100:220] + [98] * 4 + old[230:290] + [97] * 3
    segs = arms.splice_plan(old, new)
    t = {"old": old, "new": new, "segs": segs, "stats": arms.plan_stats(old, new, segs)}
    sets = latency.computed_sets(t, "e")
    lo, hi = repair.region(t)
    reused = repair.reuse_triplets(segs, lo, hi)[0]
    assert sets["recompute"] is None and sets["prefix"] == set(range(lo, len(new)))
    assert sets["shift"] < sets["epic32"] <= sets["epic128"] and sets["shift"] < sets["blend15"]
    assert len(sets["blend15"] - sets["shift"]) == -(-len(reused) * 15 // 100)
    for arm, comp in sets.items():
        if comp is None:
            continue
        ids, plan = latency.layout(t, comp, "k")
        n = plan["load"]["n"] if plan else 0
        assert len(ids) == len(new) and n == len(new) - len(comp)
        covered = 0
        for key, s_lo, s_hi, d_lo, delta, rot in (plan["load"]["segs"] if plan else []):
            assert old[s_lo:s_hi] == ids[d_lo:d_lo + s_hi - s_lo]      # a loaded row is the same token
            assert delta == d_lo - s_lo and rot == (delta != 0)
            covered += s_hi - s_lo
        assert covered == n
        assert sorted(ids[n:]) == sorted(new[p] for p in comp)

if __name__ == "__main__":      # the project venv has no pytest; run the tests directly
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:        # report every failing test, not just the first
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
