"""s15's server-aware pacing (research/18_harness_wait): the `wait` tool, the per-round server
status line and the harness-side holds. Everything must be inert when no S15_* variable is set;
when set, `wait` runs after the round's other tools, the status line is appended once per round
(never twice for a re-issued call, never to bytes already sent), holds happen only where configured,
and an unreachable status endpoint never blocks or breaks the agent."""
import copy
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from test_agent_teams_runtime import ROOT, load_lesson

S15 = ROOT / "s15_integrated_harness" / "code.py"
S15_KEYS = ("S15_WAIT_TEXT", "S15_WAIT_TOOL", "S15_STATUS_LINE", "S15_STATUS_VIEW", "S15_HOLD_POLICY", "S15_HOLD_PHASES",
            "S15_HOLD_MAX_S", "S15_WAIT_MAX_S", "S15_STATUS_URL", "S15_SESSION_ID", "S15_SHARE_TAU",
            "S15_BACKOFF_D0", "S15_BACKOFF_DMAX", "S15_SHARE_POLL_S", "S15_HOLD_SEED",
            # research/19_efficiency_signals
            "S15_TOOLS", "S15_SERVER_SECTION", "S15_RECEIPT", "S15_YIELD_MAX_S", "S15_YIELD_DEFAULT_S",
            "S15_MAX_TOKENS", "S15_MAX_TOKENS_ESCALATED", "S15_SUMMARY_MAX_TOKENS",
            # 2026-10-30: the KV rule
            "S15_STATUS_FORMAT", "S15_WAIT_KV")
TOOLBOX = "wait,wait_for_capacity,server_status"

STOCK_CALM = {"view": "stock", "age_ms": 50, "running": 3, "waiting": 0, "waiting_capacity": 0,
              "kv_usage": 0.40, "hit_rate_10s": 0.9, "preemptions_10s": 0}
STOCK_CROWDED = {"view": "stock", "age_ms": 50, "running": 12, "waiting": 5, "waiting_capacity": 4,
                 "kv_usage": 0.97, "hit_rate_10s": 0.04, "preemptions_10s": 0}


def share(ahead: int, ws: int = 20000, pool: int = 100000) -> dict:
    return {"view": "share", "age_ms": 50, "pool_tokens": pool, "running": 4, "waiting": 2,
            "me": {"sid": "S1", "ws": ws, "prefix": ws // 2, "res": ws, "ahead_ws": ahead, "ahead_n": 2,
                   "rank": 2, "mid": True}}


def full(ahead: int, ws: int = 20000, pool: int = 100000, last: dict | None = None) -> dict:
    """A share-view answer as wt_server_ext sends it: the stock fields too, and the last call's record."""
    out = share(ahead, ws, pool)
    out.update({k: v for k, v in STOCK_CROWDED.items() if k != "view"})
    if last is not None:
        out["me"] = dict(out["me"], last=last)
    return out


def with_usage(response, uncached: int, hit: int, out: int):
    response.usage = types.SimpleNamespace(input_tokens=uncached, cache_read_input_tokens=hit,
                                           cache_creation_input_tokens=None, output_tokens=out)
    return response


class FakeStatus:
    """GET /wt/status: answers from a script (the last answer repeats) and records the queries."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                outer.queries.append({k: v[0] for k, v in parse_qs(url.query).items()})
                body = outer.answers.pop(0) if len(outer.answers) > 1 else outer.answers[0]
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/wt/status"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.t += seconds


@contextmanager
def s15_env(**values):
    saved = {k: os.environ.get(k) for k in S15_KEYS}
    for k in S15_KEYS:
        os.environ.pop(k, None)
    os.environ.update({k: str(v) for k, v in values.items()})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def dump(message) -> str:
    return json.dumps(message, default=lambda o: getattr(o, "__dict__", str(o)))


def tool_use(block_id, name, args):
    return types.SimpleNamespace(type="tool_use", id=block_id, name=name, input=args)


def reply(*blocks, stop="tool_use"):
    return types.SimpleNamespace(stop_reason=stop, content=list(blocks))


TEXT_DONE = reply(types.SimpleNamespace(type="text", text="done"), stop="end_turn")


class ServerHoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fakes = []

    def tearDown(self):
        for fake in self.fakes:
            fake.close()
        self.tmp.cleanup()

    def load(self, **env):
        with s15_env(**env):
            lesson = load_lesson(Path(self.tmp.name), S15)
        lesson.MEMORY_RUNTIME = types.SimpleNamespace(
            read_memory_index=lambda: "", load_memories=lambda messages: "",
            extract_memories=lambda messages: 0, consolidate_memories=lambda: None)
        lesson.remember_after_turn_async = lambda messages: None
        lesson.trigger_hooks = lambda *args, **kwargs: None
        self.clock = FakeClock()
        lesson.HOLD.SLEEP = self.clock.sleep
        lesson.HOLD.CLOCK = self.clock.now
        return lesson

    def fake(self, answers):
        fake = FakeStatus(answers)
        self.fakes.append(fake)
        return fake

    def run_loop(self, lesson, responses, prompt="Inspect the repo."):
        sent = []
        script = iter(responses)

        def call_llm(messages, *args, **kwargs):
            sent.append(copy.deepcopy(messages))
            return next(script)

        lesson.call_llm = call_llm
        messages = [{"role": "user", "content": prompt}]
        lesson.agent_loop(messages, {}, prompt)
        return messages, sent

    # -- default off -------------------------------------------------------------------------------
    def test_default_off_changes_nothing(self):
        fake = self.fake([STOCK_CROWDED])
        lesson = self.load()                                    # no S15_* variable at all
        os.environ["ANTHROPIC_BASE_URL"] = fake.url.rsplit("/wt/status", 1)[0]
        try:
            tools, handlers = lesson.assemble_tool_pool()
            self.assertEqual(len(lesson.BUILTIN_TOOLS), 26)
            self.assertEqual([t["name"] for t in tools], [t["name"] for t in lesson.BUILTIN_TOOLS])
            self.assertNotIn("wait", handlers)
            self.assertEqual(lesson.tools_prompt(), lesson.PROMPT_SECTIONS["tools"])
            self.assertNotIn("connect_mcp, wait", lesson.assemble_system_prompt({}))
            glob = tool_use("t1", "glob", {"pattern": "*"})
            messages, sent = self.run_loop(lesson, [reply(glob), TEXT_DONE])
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        self.assertEqual(fake.queries, [], "no status request may be made by default")
        self.assertEqual(sent[0], [{"role": "user", "content": "Inspect the repo."}])
        self.assertFalse(any("[server]" in dump(m) for m in messages))
        self.assertEqual(self.clock.sleeps, [])

    # -- the wait tool ------------------------------------------------------------------------------
    def test_wait_is_advertised_only_when_enabled(self):
        lesson = self.load(S15_WAIT_TOOL=1)
        tools, handlers = lesson.assemble_tool_pool()
        self.assertEqual(tools[-1]["name"], "wait")
        self.assertIn("seconds", tools[-1]["input_schema"]["properties"])
        self.assertIn("wait", handlers)
        self.assertIn("connect_mcp, wait.", lesson.assemble_system_prompt({}))
        self.assertEqual(len(lesson.BUILTIN_TOOLS), 26)
        self.assertEqual(lesson.wait_tools()[0]["name"], "wait")

    def test_wait_text_variant(self):
        lesson = self.load(S15_WAIT_TOOL=1)
        self.assertEqual(lesson.assemble_tool_pool()[0][-1]["description"], lesson.HOLD.WAIT_TEXTS["v1"])
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v2")
        description = lesson.assemble_tool_pool()[0][-1]["description"]
        self.assertIn("still costs one model step", description)
        self.assertEqual(lesson.wait_tools()[0]["description"], description)

    def test_wait_runs_after_the_rounds_other_tools_and_is_capped(self):
        fake = self.fake([STOCK_CROWDED, STOCK_CALM])
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_MAX_S=10, S15_STATUS_URL=fake.url)
        order = []
        real_glob = lesson.BUILTIN_HANDLERS["glob"]

        def glob(**kwargs):
            order.append(("glob", self.clock.now()))
            return real_glob(**kwargs)

        lesson.BUILTIN_HANDLERS["glob"] = glob
        start = self.clock.now()
        wait = tool_use("w1", "wait", {"seconds": 30, "reason": "server crowded"})
        glob_block = tool_use("g1", "glob", {"pattern": "*"})
        rounds_before = lesson.rounds_since_todo
        messages, _ = self.run_loop(lesson, [reply(wait, glob_block), TEXT_DONE])
        self.assertEqual(order, [("glob", start)], "glob must run before the wait")
        results = messages[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["w1", "g1"], "results keep the tool_use order")
        self.assertTrue(results[0]["content"].startswith("Waited 10 s. (wait is capped at 10 s.)"),
                        results[0]["content"])
        self.assertIn("[server] running 3, waiting 0", results[0]["content"])
        self.assertAlmostEqual(sum(self.clock.sleeps), 10.0)
        self.assertEqual(lesson.rounds_since_todo, rounds_before + 1, "wait must not count toward todo reminders")
        self.assertEqual([q["state"] for q in fake.queries], ["wait", "line"])

    def test_wait_rejects_bad_seconds(self):
        lesson = self.load(S15_WAIT_TOOL=1)
        self.assertTrue(lesson.run_wait_tool("soon").startswith("Error:"))
        self.assertTrue(lesson.run_wait_tool(-3).startswith("Error:"))
        self.assertTrue(lesson.run_wait_tool(float("nan")).startswith("Error:"))
        self.assertEqual(self.clock.sleeps, [])

    def test_subagent_gets_wait_and_defers_it(self):
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_MAX_S=5)
        seen_tools = []
        script = iter([reply(tool_use("w1", "wait", {"seconds": 2}), tool_use("g1", "glob", {"pattern": "*"})),
                       reply(types.SimpleNamespace(type="text", text="summary"), stop="end_turn")])

        def create(**kwargs):
            seen_tools.append([t["name"] for t in kwargs["tools"]])
            return next(script)

        lesson.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        self.assertEqual(lesson.spawn_subagent("look around"), "summary")
        self.assertEqual(seen_tools[0][-1], "wait")
        self.assertAlmostEqual(sum(self.clock.sleeps), 2.0)

    # -- the status line ----------------------------------------------------------------------------
    def test_status_line_once_per_round_append_only_and_not_on_retry(self):
        fake = self.fake([STOCK_CROWDED])
        lesson = self.load(S15_STATUS_LINE=1, S15_STATUS_URL=fake.url)
        glob = tool_use("g1", "glob", {"pattern": "*"})
        first = reply(types.SimpleNamespace(type="text", text="..."), stop="max_tokens")  # -> re-issued call
        messages, sent = self.run_loop(lesson, [first, reply(glob), TEXT_DONE])
        line = "[server] running 12, waiting 5 (4 for KV memory); KV memory in use 97%; " \
               "prompt-cache hits 4% and preemptions 0 in the last 10 s"
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0][0]["content"], [{"type": "text", "text": "Inspect the repo."},
                                                 {"type": "text", "text": line}])
        self.assertEqual(sent[1], sent[0], "the re-issued call must not get a second line")
        self.assertEqual(sent[2][:-1], sent[1][:1] + [messages[1]], "earlier bytes never change")
        self.assertEqual(sent[2][-1]["content"][-1], {"type": "text", "text": line})
        self.assertEqual(sum(dump(m).count("[server]") for m in sent[2]), 2)
        self.assertEqual(len(fake.queries), 2)

    def test_share_line_format(self):
        lesson = self.load(S15_STATUS_VIEW="share")
        self.assertEqual(lesson.HOLD.format_line(share(231040, ws=24512, pool=258192)),
                         "[server] KV pool 258,192 tokens; sessions ahead of you hold 231,040; your context "
                         "24,512, 12,256 still cached; 2 requests waiting")

    # -- harness holds ------------------------------------------------------------------------------
    def test_backoff_holds_only_after_tool_rounds_until_clear(self):
        crowded_but_clearing = dict(STOCK_CROWDED, waiting_capacity=0, waiting=1, kv_usage=0.90)
        fake = self.fake([STOCK_CROWDED, STOCK_CROWDED, crowded_but_clearing, STOCK_CALM])
        lesson = self.load(S15_HOLD_POLICY="backoff", S15_STATUS_URL=fake.url, S15_HOLD_SEED="t")
        glob = tool_use("g1", "glob", {"pattern": "*"})
        messages, sent = self.run_loop(lesson, [reply(glob), TEXT_DONE])
        states = [q["state"] for q in fake.queries]
        self.assertEqual(states, ["check", "hold", "hold", "hold"], states)   # none before the first call
        self.assertEqual(len(self.clock.sleeps), 3)
        self.assertTrue(1.0 <= self.clock.sleeps[0] <= 3.0 and 2.0 <= self.clock.sleeps[1] <= 6.0, self.clock.sleeps)
        self.assertEqual(sent[1][:2], sent[0][:1] + [messages[1]], "a hold changes no message")
        self.assertFalse(any("[server]" in dump(m) for m in messages))

    def test_share_holds_while_the_sessions_ahead_fill_the_pool(self):
        fake = self.fake([share(85000), share(85000), share(60000)])
        lesson = self.load(S15_HOLD_POLICY="share", S15_STATUS_URL=fake.url, S15_SESSION_ID="S1",
                           S15_SHARE_TAU=0.9)
        held = lesson.HOLD.maybe_hold("mid", None)
        self.assertEqual([q["state"] for q in fake.queries], ["check", "hold", "hold"])
        self.assertEqual({q["view"] for q in fake.queries}, {"share"})
        self.assertEqual({q.get("sid") for q in fake.queries}, {"S1"})
        self.assertAlmostEqual(held, sum(self.clock.sleeps))
        self.assertEqual(lesson.HOLD.maybe_hold("start", None), 0.0, "start is not a configured phase")

    def test_hold_stops_at_the_cap(self):
        fake = self.fake([share(99000)])
        lesson = self.load(S15_HOLD_POLICY="share", S15_STATUS_URL=fake.url, S15_SESSION_ID="S1",
                           S15_HOLD_MAX_S=7)
        self.assertAlmostEqual(lesson.HOLD.maybe_hold("mid", None), 7.0)

    def test_unreachable_status_fails_open_and_rests(self):
        lesson = self.load(S15_HOLD_POLICY="backoff", S15_STATUS_LINE=1, S15_WAIT_TOOL=1,
                           S15_STATUS_URL="http://127.0.0.1:9/wt/status")
        self.assertEqual(lesson.HOLD.maybe_hold("mid", None), 0.0)
        messages = [{"role": "user", "content": "x"}]
        self.assertEqual(lesson.HOLD.append_status_line(messages), "")
        self.assertEqual(messages, [{"role": "user", "content": "x"}])
        self.assertEqual(lesson.run_wait_tool(3), "Waited 3 s.")
        before = lesson.HOLD._state["requests"]
        lesson.HOLD.maybe_hold("mid", None)
        self.assertEqual(lesson.HOLD._state["requests"], before, "after 3 failures the client rests")

    def test_stale_status_counts_as_no_status(self):
        fake = self.fake([dict(STOCK_CROWDED, age_ms=60000)])
        lesson = self.load(S15_HOLD_POLICY="backoff", S15_STATUS_URL=fake.url)
        self.assertEqual(lesson.HOLD.maybe_hold("mid", None), 0.0)

    # -- research/19_efficiency_signals: the toolbox, the server paragraph, the receipt -----------------
    def test_toolbox_and_paragraph_only_when_enabled(self):
        lesson = self.load()
        self.assertEqual(lesson.HOLD.server_section(), "")
        self.assertNotIn("Shared server", lesson.assemble_system_prompt({}))
        self.assertEqual(lesson.wait_tools(), [])
        lesson = self.load(S15_TOOLS=TOOLBOX, S15_WAIT_TEXT="v3", S15_SERVER_SECTION="tools")
        tools, handlers = lesson.assemble_tool_pool()
        self.assertEqual([t["name"] for t in tools[-3:]], ["wait", "wait_for_capacity", "server_status"])
        self.assertEqual(len(tools), len(lesson.BUILTIN_TOOLS) + 3)
        self.assertEqual(len(lesson.BUILTIN_TOOLS), 26)
        self.assertTrue({"wait", "wait_for_capacity", "server_status"} <= set(handlers))
        self.assertEqual(tools[-3]["description"], lesson.HOLD.WAIT_TEXTS["v3"])
        system = lesson.assemble_system_prompt({})
        self.assertIn("connect_mcp, wait, wait_for_capacity, server_status.", system)
        self.assertIn(lesson.PROMPT_SECTIONS["efficiency"] + "\n\n" + lesson.HOLD.SERVER_SECTIONS["tools"]
                      + "\n\nSkills catalog:", system)
        lesson = self.load(S15_WAIT_TOOL=1, S15_SERVER_SECTION="wait")
        self.assertIn(lesson.HOLD.SERVER_SECTIONS["wait"], lesson.assemble_system_prompt({}))
        self.assertEqual([t["name"] for t in lesson.wait_tools()], ["wait"])
        lesson = self.load(S15_TOOLS="server_status,bogus", S15_SERVER_SECTION="nonsense")
        self.assertEqual([t["name"] for t in lesson.wait_tools()], ["server_status"])
        self.assertEqual(lesson.HOLD.server_section(), "")

    def test_wait_for_capacity_runs_last_and_ends_when_the_session_fits(self):
        fake = self.fake([full(85000), full(85000), full(60000)])
        lesson = self.load(S15_TOOLS=TOOLBOX, S15_STATUS_URL=fake.url, S15_SESSION_ID="S1", S15_HOLD_SEED="t")
        order = []
        real_glob = lesson.BUILTIN_HANDLERS["glob"]

        def glob(**kwargs):
            order.append(len(fake.queries))
            return real_glob(**kwargs)

        lesson.BUILTIN_HANDLERS["glob"] = glob
        step_back = tool_use("y1", "wait_for_capacity", {"max_seconds": 120, "reason": "crowded"})
        glob_block = tool_use("g1", "glob", {"pattern": "*"})
        messages, _ = self.run_loop(lesson, [reply(step_back, glob_block), TEXT_DONE])
        self.assertEqual(order, [0], "glob must run before the wait (no status read yet)")
        results = messages[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["y1", "g1"], "results keep the tool_use order")
        text = results[0]["content"]
        self.assertTrue(text.startswith(f"Waited {sum(self.clock.sleeps):.0f} s: there is room for your context "
                                        "now.\n[server] running 12"), text)
        self.assertIn("; KV pool 100,000 tokens; sessions ahead of you hold 60,000", text)
        self.assertEqual([q["state"] for q in fake.queries], ["wait", "wait", "wait"])
        self.assertEqual({q["view"] for q in fake.queries}, {"share"})
        self.assertEqual(len(self.clock.sleeps), 2)

    def test_wait_for_capacity_stops_at_its_cap_and_fails_open(self):
        fake = self.fake([full(99000)])
        lesson = self.load(S15_TOOLS=TOOLBOX, S15_STATUS_URL=fake.url, S15_SESSION_ID="S1", S15_YIELD_MAX_S=7)
        text = lesson.run_wait_for_capacity_tool(30)
        self.assertTrue(text.startswith("Waited 7 s: the 7 s limit was reached and the server is still crowded."),
                        text)
        self.assertAlmostEqual(sum(self.clock.sleeps), 7.0)
        self.assertTrue(lesson.run_wait_for_capacity_tool("soon").startswith("Error:"))
        self.assertTrue(lesson.run_wait_for_capacity_tool(0).startswith("Error:"))
        lesson = self.load(S15_TOOLS=TOOLBOX, S15_STATUS_URL="http://127.0.0.1:9/wt/status", S15_SESSION_ID="S1")
        self.assertEqual(lesson.run_wait_for_capacity_tool(), "Waited 0 s: the server status is not available.")
        self.assertEqual(self.clock.sleeps, [])

    def test_server_status_runs_in_order_and_is_not_work(self):
        fake = self.fake([full(30000)])
        lesson = self.load(S15_TOOLS="server_status", S15_STATUS_URL=fake.url, S15_SESSION_ID="S1")
        pull = tool_use("s1", "server_status", {})
        glob_block = tool_use("g1", "glob", {"pattern": "*"})
        rounds_before = lesson.rounds_since_todo
        messages, _ = self.run_loop(lesson, [reply(pull, glob_block), TEXT_DONE])
        results = messages[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["s1", "g1"])
        self.assertEqual(results[0]["content"],
                         "[server] running 12, waiting 5 (4 for KV memory); KV memory in use 97%; prompt-cache "
                         "hits 4% and preemptions 0 in the last 10 s; KV pool 100,000 tokens; sessions ahead of "
                         "you hold 30,000; your context 20,000, 10,000 still cached")
        self.assertEqual([q["state"] for q in fake.queries], ["pull"])
        self.assertEqual(lesson.rounds_since_todo, rounds_before + 1, "a status read must not count as work")

    def test_receipt_reports_the_last_step_once_per_round(self):
        last = {"np": 24512, "ideal": 23110, "hit": 18304, "wait_s": 4.1}
        fake = self.fake([full(30000, last=last)])
        lesson = self.load(S15_RECEIPT=1, S15_STATUS_URL=fake.url, S15_SESSION_ID="S1")
        glob = tool_use("g1", "glob", {"pattern": "*"})
        cut = reply(types.SimpleNamespace(type="text", text="..."), stop="max_tokens")      # -> re-issued call
        responses = [with_usage(reply(glob), 6208, 18304, 412), with_usage(cut, 900, 24000, 8000),
                     with_usage(reply(glob), 100, 24000, 50), with_usage(TEXT_DONE, 10, 24100, 5)]
        messages, sent = self.run_loop(lesson, responses)
        self.assertEqual(sent[0], [{"role": "user", "content": "Inspect the repo."}], "no receipt before a step")
        self.assertEqual(sent[1][-1]["content"][-1]["text"],
                         "[cost] your last step: 24,512 prompt tokens, 18,304 reused from the server's cache, "
                         "4,806 recomputed because they had been evicted, 1,402 new or changed; queued 4.1 s; "
                         "412 tokens generated; 0.0 s in all")
        self.assertEqual(sent[2], sent[1], "the re-issued call must not get a second receipt")
        self.assertEqual(sent[3][-1]["content"][-1]["text"],       # the server's record is not this call's
                         "[cost] your last step: 24,100 prompt tokens, 24,000 reused from the server's cache, "
                         "100 computed; 50 tokens generated; 0.0 s in all")
        self.assertEqual(sent[3][:len(sent[1])], sent[1], "earlier bytes never change")
        self.assertEqual(len(fake.queries), 2)
        self.assertEqual({q["view"] for q in fake.queries}, {"share"})
        self.assertFalse(any("[server]" in dump(m) for m in messages))

    def test_output_budgets_from_env_and_summary_drops_reasoning(self):
        lesson = self.load()
        self.assertEqual((lesson.DEFAULT_MAX_TOKENS, lesson.ESCALATED_MAX_TOKENS, lesson.SUMMARY_MAX_TOKENS),
                         (8000, 16000, 2000))
        lesson = self.load(S15_MAX_TOKENS=16000, S15_MAX_TOKENS_ESCALATED=32000, S15_SUMMARY_MAX_TOKENS=8000)
        self.assertEqual((lesson.DEFAULT_MAX_TOKENS, lesson.ESCALATED_MAX_TOKENS, lesson.SUMMARY_MAX_TOKENS),
                         (16000, 32000, 8000))
        seen = []

        def create(**kwargs):
            seen.append(kwargs["max_tokens"])
            return reply(types.SimpleNamespace(type="text", text="<think>\nweigh it\n</think>\n\nGoal: fix X."),
                         stop="end_turn")

        lesson.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        self.assertEqual(lesson.summarize_history([{"role": "user", "content": "hi"}]), "Goal: fix X.")
        self.assertEqual(seen, [8000])

    def test_subagent_gets_the_toolbox(self):
        fake = self.fake([full(30000)])
        lesson = self.load(S15_TOOLS=TOOLBOX, S15_STATUS_URL=fake.url, S15_SESSION_ID="S1")
        seen_tools, sent = [], []
        script = iter([reply(tool_use("y1", "wait_for_capacity", {"max_seconds": 5}),
                             tool_use("s1", "server_status", {})),
                       reply(types.SimpleNamespace(type="text", text="summary"), stop="end_turn")])

        def create(**kwargs):
            seen_tools.append([t["name"] for t in kwargs["tools"]])
            sent.append(copy.deepcopy(kwargs["messages"]))
            return next(script)

        lesson.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        self.assertEqual(lesson.spawn_subagent("look around"), "summary")
        self.assertEqual(seen_tools[0][-3:], ["wait", "wait_for_capacity", "server_status"])
        results = sent[1][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["y1", "s1"])
        self.assertTrue(results[0]["content"].startswith("Waited 0 s: there is room"), results[0]["content"])
        self.assertTrue(results[1]["content"].startswith("[server] running 12"), results[1]["content"])
        self.assertEqual([q["state"] for q in fake.queries], ["pull", "wait"], "the status read runs in order")

    # -- 2026-10-30: the KV rule (wait text v5, the paragraph wait_kv, the kv and kv_cue lines) ---------------------
    def test_kv_rule_texts_name_the_threshold(self):
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v5", S15_SERVER_SECTION="wait_kv")
        description = lesson.assemble_tool_pool()[0][-1]["description"]
        self.assertIn("shows the KV cache 80% full or more, include one wait call", description)
        self.assertIn("Below 80%, do not wait", description)
        system = lesson.assemble_system_prompt({})
        self.assertIn(lesson.PROMPT_SECTIONS["efficiency"] + "\n\n" + lesson.HOLD.server_section(), system)
        self.assertIn("Server rule: when that line shows 80% or more, include a wait call", system)
        self.assertNotIn("{kv}", description + system)
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v5", S15_SERVER_SECTION="wait_kv", S15_WAIT_KV=0.75)
        self.assertIn("KV cache 75% full or more", lesson.wait_tools()[0]["description"])
        self.assertIn("shows 75% or more", lesson.HOLD.server_section())
        for bad in ("1.5", "0", "soon"):
            self.assertEqual(self.load(S15_WAIT_KV=bad).HOLD.CONFIG.wait_kv, 0.8, bad)
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v6", S15_SERVER_SECTION="wait_kv")
        description = lesson.wait_tools()[0]["description"]
        self.assertTrue(description.startswith(lesson.HOLD._with_kv(lesson.HOLD.WAIT_TEXTS["v5"]) + " Example: "))
        self.assertIn('reads "[server] KV cache 90% full; 12 requests waiting", call your next tools and add wait',
                      description)
        self.assertIn('reads "[server] KV cache 55% full; 2 requests waiting", do not call wait.', description)
        self.assertNotIn("{kv", description)
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v6", S15_WAIT_KV=0.75)
        self.assertIn("KV cache 85% full;", lesson.wait_tools()[0]["description"])
        self.assertIn("KV cache 50% full;", lesson.wait_tools()[0]["description"])

    def test_kv_lines_and_the_cue(self):
        lesson = self.load(S15_STATUS_FORMAT="kv")
        self.assertEqual(lesson.HOLD.round_line(STOCK_CROWDED), "[server] KV cache 97% full; 5 requests waiting")
        self.assertTrue(lesson.HOLD.format_full(STOCK_CROWDED).startswith("[server] running 12"),
                        "server_status keeps the full wording")
        lesson = self.load(S15_STATUS_FORMAT="kv_cue")
        cue = ". At 80% or more: add a wait call to your response."
        for kv, shown, cued in ((0.40, "40%", False), (0.79, "79%", False), (0.796, "80%", True), (0.80, "80%", True),
                                (0.97, "97%", True)):
            self.assertEqual(lesson.HOLD.round_line(dict(STOCK_CROWDED, kv_usage=kv)),
                             f"[server] KV cache {shown} full; 5 requests waiting" + (cue if cued else ""), kv)
        no_kv = {k: v for k, v in STOCK_CROWDED.items() if k != "kv_usage"}
        self.assertEqual(lesson.HOLD.round_line(no_kv), "[server] KV cache n/a full; 5 requests waiting")
        self.assertEqual(lesson.HOLD.round_line(None), "")
        lesson = self.load(S15_STATUS_FORMAT="bogus")
        self.assertEqual(lesson.HOLD.round_line(STOCK_CROWDED), lesson.HOLD.format_line(STOCK_CROWDED))

    def test_kv_cue_line_every_round_and_after_a_wait(self):
        fake = self.fake([STOCK_CROWDED, STOCK_CROWDED, STOCK_CALM, STOCK_CALM])
        lesson = self.load(S15_WAIT_TOOL=1, S15_WAIT_TEXT="v5", S15_SERVER_SECTION="wait_kv", S15_STATUS_LINE=1,
                           S15_STATUS_FORMAT="kv_cue", S15_WAIT_MAX_S=10, S15_STATUS_URL=fake.url)
        glob_block = tool_use("g1", "glob", {"pattern": "*"})
        wait = tool_use("w1", "wait", {"seconds": 15})
        messages, sent = self.run_loop(lesson, [reply(glob_block, wait), TEXT_DONE])
        crowded = "[server] KV cache 97% full; 5 requests waiting. At 80% or more: add a wait call to your response."
        calm = "[server] KV cache 40% full; 0 requests waiting"
        self.assertEqual(sent[0][0]["content"][-1], {"type": "text", "text": crowded})
        results = [b for b in messages[2]["content"] if b.get("type") == "tool_result"]
        self.assertEqual([r["tool_use_id"] for r in results], ["g1", "w1"])
        self.assertEqual(results[1]["content"], f"Waited 10 s. (wait is capped at 10 s.)\n{calm}")
        self.assertEqual(sent[1][-1]["content"][-1], {"type": "text", "text": calm})
        self.assertEqual([q["state"] for q in fake.queries], ["line", "wait", "line", "line"])

    def test_old_setups_match_topic19s_frozen_copy(self):
        """The KV rule is additive: every earlier setup sends what it sent in topics 18-19, byte for byte."""
        path = ROOT / "research" / "19_efficiency_signals" / "s15_integrated_harness" / "hold_runtime.py"
        if not path.exists():
            self.skipTest("topic 19's frozen copy of hold_runtime.py is not in this checkout")
        spec = importlib.util.spec_from_file_location("frozen_t19_hold_runtime", path)
        frozen = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = frozen                       # dataclasses look the module up while it loads
        try:
            with s15_env():
                spec.loader.exec_module(frozen)
        finally:
            sys.modules.pop(spec.name, None)
        live = self.load().HOLD
        for name, text in frozen.WAIT_TEXTS.items():
            self.assertEqual(live.WAIT_TEXTS[name], text, name)
        for name, text in frozen.SERVER_SECTIONS.items():
            self.assertEqual(live.SERVER_SECTIONS[name], text, name)
        statuses = (STOCK_CALM, STOCK_CROWDED, share(231040), full(30000))
        setups = [{}, {"S15_WAIT_TOOL": 1}, {"S15_WAIT_TOOL": 1, "S15_WAIT_TEXT": "v2", "S15_STATUS_LINE": 1},
                  {"S15_TOOLS": "wait", "S15_WAIT_TEXT": "v3", "S15_SERVER_SECTION": "wait", "S15_STATUS_LINE": 1,
                   "S15_STATUS_VIEW": "share"},
                  {"S15_TOOLS": "wait", "S15_WAIT_TEXT": "v4", "S15_SERVER_SECTION": "wait_v2", "S15_STATUS_LINE": 1},
                  {"S15_TOOLS": TOOLBOX, "S15_WAIT_TEXT": "v3", "S15_SERVER_SECTION": "tools", "S15_RECEIPT": 1}]
        for env in setups:
            live = self.load(**env).HOLD
            with s15_env(**env):
                frozen.reload()
            self.assertEqual(live.pacing_tools(), frozen.pacing_tools(), env)
            self.assertEqual(live.server_section(), frozen.server_section(), env)
            for status in statuses:
                self.assertEqual(live.round_line(status), frozen.format_line(status), env)
                self.assertEqual(live.format_full(status), frozen.format_full(status), env)


if __name__ == "__main__":
    unittest.main()
