"""Offline unit test of the three context-hierarchy interventions (no provider calls)."""
import importlib.util, json, os, sys, types
from pathlib import Path
REPO = Path("/home/yq335/learn-claude-code")
os.environ["HARNESS_TRACE"] = "0"
os.chdir(REPO)

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m

pr = load("pr", REPO / "s15_integrated_harness/scripts/profile_run.py")
mod = load("s15t", REPO / "s15_integrated_harness/code.py")

FILES = ["s01_agent_loop/README.md", "s02_tool_use/README.md", "s03_permission/README.md",
         "s04_hooks/README.md", "s05_todo_write/README.md", "s06_subagent/README.md"]
calls = {f"tu{i}": {"tool": "read_file", "input": {"path": p}} for i, p in enumerate(FILES)}
args = types.SimpleNamespace(batch_read=True, batch_read_max=12, batch_read_max_chars=0, skeleton_demote=True,
                             stable_prefix=True, no_timestamp=False, snip_keep_tail=12, deny_bash=False)
mod.CONTEXT_LIMIT = 30000
pr.apply_interventions(mod, args, calls)
ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {name}")
    else: fail += 1; print(f"  FAIL  {name}  {detail}")

print("== intervention 1: read_files")
tools, handlers = mod.assemble_tool_pool()
names = [t["name"] for t in tools]
check("read_files in the tool pool", "read_files" in names, names[-3:])
check("pool grew by exactly one tool", len(tools) == 27, len(tools))
out = handlers["read_files"](files=[{"path": FILES[0], "limit": 3}, {"path": FILES[1]}])
check("returns both files with headers", out.count("=====") == 4 and FILES[0] in out and FILES[1] in out)
check("honours limit", "... (" in out.split("=====")[2])
ALL17 = sorted(str(q.relative_to(REPO)) for q in REPO.glob("s??_*/README.md"))
big = handlers["read_files"](files=[{"path": q} for q in ALL17])
cap = max(8000, int(mod.CONTEXT_LIMIT * 0.25))
check("batch stops at the per-call char budget", len(big) < cap + 25000, f"{len(big):,} chars, cap {cap:,}")
check("batch says which entries it skipped", "were NOT read because" in big, big[-200:])
check("batch would survive fit_tool_results (< 0.8*limit)", len(big) < mod.CONTEXT_LIMIT * 0.8,
      f"{len(big):,} vs {mod.CONTEXT_LIMIT*0.8:,.0f}")
esc = handlers["read_files"](files=[{"path": "../../etc/passwd"}])
check("path escape contained", "Error" in esc, esc[:80])
bad = handlers["read_files"](files="not-a-list")
check("tolerates a string argument", isinstance(bad, str))
check("prompt advertises read_files", "read_files" in mod.PROMPT_SECTIONS["tools"])

print("== intervention 4a: no per-second timestamp in the system prompt")
sp = mod.assemble_system_prompt({})
check("no 'Current time:' section", "Current time:" not in sp)

print("== intervention 2: skeleton demotion")
messages = [{"role": "user", "content": "compare the chapters"}]
for i, path in enumerate(FILES):
    body = (REPO / path).read_text(encoding="utf-8")
    messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"tu{i}", "name": "read_file",
                                                       "input": {"path": path}}]})
    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu{i}", "content": body}]})
messages.append({"role": "assistant", "content": [{"type": "text", "text": "thinking about it"}]})
before = mod.estimate_size(messages)
mod.prepare_context(messages, "compare the chapters")
after = mod.estimate_size(messages)
blocks = [b for _, _, b in mod.collect_tool_results(messages)]
demoted = [str(b["content"]) for b in blocks if str(b["content"]).startswith("[Earlier tool result saved at ")]
check("history shrank", after < before, f"{before} -> {after}")
check("demoted all but the 3 newest", len(demoted) == 3, len(demoted))
check("outline carries offsets", all("offset " in d for d in demoted))
check("names the source file", any("demoted file s01_agent_loop/README.md" in d for d in demoted), demoted[0][:120])
check("outline is small", all(len(d) < 2000 for d in demoted), max(len(d) for d in demoted))
spill = mod.persisted_output_path(demoted[0])
check("spill path still resolves", bool(spill), demoted[0].splitlines()[0])
if spill:
    saved = Path(spill).read_text(encoding="utf-8")
    check("spill file holds the ORIGINAL text, not the outline",
          saved == (REPO / FILES[0]).read_text(encoding="utf-8"), saved[:60])
mod.CONTEXT_LIMIT = 12000  # force eviction for the two focused cases below
# outline offsets must be offsets in the FILE, not in the returned window
win_msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "w0", "name": "read_file",
                                               "input": {"path": FILES[0], "offset": 100, "limit": 60}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w0",
                                          "content": "\n".join((REPO / FILES[0]).read_text().splitlines()[100:160])}]}]
for i, path in enumerate(FILES[1:5], start=10):
    win_msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"w{i}", "name": "read_file",
                                                       "input": {"path": path}}]})
    win_msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"w{i}",
                                                  "content": (REPO / path).read_text(encoding="utf-8")}]})
win_msgs.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
calls["w0"] = {"tool": "read_file", "input": {"path": FILES[0], "offset": 100, "limit": 60}}
for i, path in enumerate(FILES[1:5], start=10):
    calls[f"w{i}"] = {"tool": "read_file", "input": {"path": path}}
mod.prepare_context(win_msgs, "go")
w0 = next(str(b["content"]) for _, _, b in mod.collect_tool_results(win_msgs) if b["tool_use_id"] == "w0")
offsets = [int(m) for m in __import__("re").findall(r"offset\s+(\d+) \|", w0)]
check("windowed read is demoted", w0.startswith("[Earlier tool result saved at "), w0[:60])
check("outline offsets are rebased to the source file", offsets and min(offsets) >= 100, offsets[:5])
check("header does not over-promise for a windowed read", "offsets in that file" in w0, w0.splitlines()[1][:130])

# a read_files result must be outlined per file, with each file's own offsets
args.batch_read_max_chars = 40000   # this case tests the outline, not the cap
batch_text = handlers["read_files"](files=[{"path": FILES[1]}, {"path": FILES[2], "offset": 40}])
args.batch_read_max_chars = 0
check("uncapped batch holds both files", batch_text.count("=====") == 4, batch_text[:80])
bmsgs = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": [{"type": "tool_use", "id": "b0", "name": "read_files",
                                            "input": {"files": [{"path": FILES[1]}, {"path": FILES[2], "offset": 40}]}}]},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b0", "content": batch_text}]}]
for i, path in enumerate(FILES[3:], start=20):
    bmsgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"b{i}", "name": "read_file",
                                                    "input": {"path": path}}]})
    bmsgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"b{i}",
                                               "content": (REPO / path).read_text(encoding="utf-8")}]})
    calls[f"b{i}"] = {"tool": "read_file", "input": {"path": path}}
bmsgs.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
calls["b0"] = {"tool": "read_files", "input": {"files": [{"path": FILES[1]}, {"path": FILES[2], "offset": 40}]}}
mod.prepare_context(bmsgs, "go")
b0 = next(str(b["content"]) for _, _, b in mod.collect_tool_results(bmsgs) if b["tool_use_id"] == "b0")
check("batch result is demoted per file", b0.count("  --- ") == 2, b0[:200])
check("each section names its file", FILES[1] in b0 and FILES[2] in b0)
sec2 = b0.split("  --- ")[2] if b0.count("  --- ") >= 2 else ""
sec2_offsets = [int(m) for m in __import__("re").findall(r"offset\s+(\d+) \|", sec2)]
check("second section uses its own file offsets", sec2_offsets and min(sec2_offsets) >= 40, sec2_offsets[:5])

mod.CONTEXT_LIMIT = 30000
print("== persisted_preview must stay baseline (attribution)")
prev = mod.persisted_preview("tuX", "x" * 5000)
check("persisted_preview still emits the baseline Preview form",
      prev.startswith("<persisted-output>") and "Preview:" in prev and "Outline" not in prev, prev[:80])

snapshot = [str(b["content"]) for b in blocks]
mod.prepare_context(messages, "compare the chapters")
check("second pass is idempotent (no re-demote)",
      [str(b["content"]) for b in blocks] == snapshot)
if spill:
    check("spill file still intact after a second pass",
          Path(spill).read_text(encoding="utf-8") == (REPO / FILES[0]).read_text(encoding="utf-8"))

print("== intervention 4b/4c: stable marker and batched eviction")
long_history = [{"role": "user", "content": "go"}]
for i in range(30):
    long_history.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"x{i}", "name": "glob",
                                                           "input": {"pattern": "*"}}]})
    long_history.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"x{i}",
                                                      "content": "a line\n" * 40}]})
first = mod.snip_compact(list(long_history))
second = mod.snip_compact(list(long_history))
markers = [m["content"] for m in first if isinstance(m.get("content"), str) and "archived at" in m["content"]]
check("archive marker present", len(markers) == 1, markers)
check("marker text is byte-identical across calls",
      markers == [m["content"] for m in second if isinstance(m.get("content"), str) and "archived at" in m["content"]])
check("marker path is the single fixed archive", markers and markers[0].endswith(".transcripts/archive.jsonl]"), markers)
check("snip drops to the low-water mark", len(first) <= 3 + 1 + args.snip_keep_tail + 1, len(first))
# batched eviction: after one eviction the next prepare_context must not rewrite anything
msgs2 = [{"role": "user", "content": "go"}]
for i, path in enumerate(FILES):
    body = (REPO / path).read_text(encoding="utf-8")
    msgs2.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"tu{i}", "name": "read_file",
                                                    "input": {"path": path}}]})
    msgs2.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu{i}", "content": body}]})
msgs2.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
mod.prepare_context(msgs2, "go")
size_after = mod.estimate_size(msgs2)
# the floor is KEEP_RECENT_TOOL_RESULTS (3) untouched results, so headroom depends on their size
check("batched eviction drops below the limit",
      size_after < mod.CONTEXT_LIMIT, f"{size_after} vs limit {mod.CONTEXT_LIMIT}")
kept = sum(len(str(b["content"])) for _, _, b in mod.collect_tool_results(msgs2)
           if not str(b["content"]).startswith("[Earlier tool result saved at "))
print(f"  NOTE  after batched eviction: {size_after:,} chars of {mod.CONTEXT_LIMIT:,} limit; "
      f"{kept:,} of those are the {mod.KEEP_RECENT_TOOL_RESULTS} newest results kept verbatim "
      f"-> headroom for appends is {mod.CONTEXT_LIMIT - size_after:,} chars")
frozen = json.dumps(msgs2, default=str)
msgs2.append({"role": "user", "content": [{"type": "text", "text": "next round"}]})
mod.prepare_context(msgs2, "go")
check("next round appends without rewriting the prefix",
      json.dumps(msgs2[:-1], default=str) == frozen)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
