"""s15's server-aware pacing (research/18_harness_wait): the `wait` tool, the per-round server
status line and the harness-side holds. Everything must be inert when no S15_* variable is set;
when set, `wait` runs after the round's other tools, the status line is appended once per round
(never twice for a re-issued call, never to bytes already sent), holds happen only where configured,
and an unreachable status endpoint never blocks or breaks the agent."""
import copy
import json
import os
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
            "S15_BACKOFF_D0", "S15_BACKOFF_DMAX", "S15_SHARE_POLL_S", "S15_HOLD_SEED")

STOCK_CALM = {"view": "stock", "age_ms": 50, "running": 3, "waiting": 0, "waiting_capacity": 0,
              "kv_usage": 0.40, "hit_rate_10s": 0.9, "preemptions_10s": 0}
STOCK_CROWDED = {"view": "stock", "age_ms": 50, "running": 12, "waiting": 5, "waiting_capacity": 4,
                 "kv_usage": 0.97, "hit_rate_10s": 0.04, "preemptions_10s": 0}


def share(ahead: int, ws: int = 20000, pool: int = 100000) -> dict:
    return {"view": "share", "age_ms": 50, "pool_tokens": pool, "running": 4, "waiting": 2,
            "me": {"sid": "S1", "ws": ws, "prefix": ws // 2, "res": ws, "ahead_ws": ahead, "ahead_n": 2,
                   "rank": 2, "mid": True}}


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


if __name__ == "__main__":
    unittest.main()
