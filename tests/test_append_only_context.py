"""s15's lead request must stay append-only: the system prompt is built once per
session and never changes, and context that changes during the session (recalled
memory, catalog updates, MCP connections) is appended to the newest, unsent user
message instead, so a prefix cache keeps every earlier token of the request."""
import copy
import json
import tempfile
import types
import unittest
from pathlib import Path

from test_agent_teams_runtime import ROOT, load_lesson

S15 = ROOT / "s15_integrated_harness" / "code.py"


def fake_memory(catalog: dict, recall: dict):
    return types.SimpleNamespace(
        read_memory_index=lambda: catalog["text"],
        load_memories=lambda messages: recall["text"],
        extract_memories=lambda messages: 0,
        consolidate_memories=lambda: None,
    )


class AppendOnlyContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lesson = load_lesson(Path(self.tmp.name), S15)
        self.catalog = {"text": "- [Style](style.md) - Project style"}
        self.recall = {"text": ""}
        self.lesson.MEMORY_RUNTIME = fake_memory(self.catalog, self.recall)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, messages, context):
        """What agent_loop does before each lead call."""
        context = self.lesson.update_context(context, messages)
        self.lesson.append_context_updates(messages, context)
        return context, self.lesson.assemble_system_prompt(context)

    def test_system_prompt_is_static_across_calls(self):
        messages = [{"role": "user", "content": "Inspect the repo."}]
        context, first = self.prepare(messages, {})
        self.assertNotIn("Current time", first)
        self.assertIn("Memory catalog", first)

        self.recall["text"] = '[{"source": "style.md", "content": "Use black."}]'
        self.catalog["text"] += "\n- [Tests](tests.md) - How to test"
        self.lesson.mcp_clients["docs"] = types.SimpleNamespace(tools=[])
        try:
            messages += [{"role": "assistant", "content": "ok"},
                         {"role": "user", "content": "continue"}]
            context, second = self.prepare(messages, context)
        finally:
            self.lesson.mcp_clients.pop("docs", None)
        self.assertEqual(first, second)
        self.assertNotIn("Connected MCP servers", second)
        self.assertNotIn("Relevant memory records", second)

    def test_updates_are_appended_only_to_the_newest_user_message(self):
        messages = [{"role": "user", "content": "Inspect the repo."}]
        context, _ = self.prepare(messages, {})
        self.assertEqual(messages, [{"role": "user",
                                     "content": "Inspect the repo."}])

        messages += [{"role": "assistant",
                      "content": [{"type": "tool_use", "id": "t1",
                                   "name": "glob", "input": {"pattern": "*"}}]},
                     {"role": "user",
                      "content": [{"type": "tool_result", "tool_use_id": "t1",
                                   "content": "a.py"}]}]
        sent = copy.deepcopy(messages[:-1])
        self.recall["text"] = '[{"source": "style.md", "content": "Use black."}]'
        context, _ = self.prepare(messages, context)

        self.assertEqual(messages[:-1], sent)          # nothing already sent changes
        newest = messages[-1]["content"]
        self.assertEqual(newest[0]["type"], "tool_result")
        self.assertIn("<memory-recall>", newest[-1]["text"])
        self.assertIn("Use black.", newest[-1]["text"])

        # The same recall is not delivered twice.
        messages += [{"role": "assistant", "content": "done"},
                     {"role": "user", "content": "next"}]
        context, _ = self.prepare(messages, context)
        self.assertEqual(messages[-1]["content"], "next")

    def test_catalog_change_is_appended_once(self):
        messages = [{"role": "user", "content": "first"}]
        context, system = self.prepare(messages, {})
        self.catalog["text"] = "- [New](new.md) - Added later"
        messages += [{"role": "assistant", "content": "ok"},
                     {"role": "user", "content": "second"}]
        context, again = self.prepare(messages, context)
        self.assertEqual(system, again)
        texts = [block["text"] for block in messages[-1]["content"]]
        self.assertEqual(texts[0], "second")
        self.assertIn("<memory-catalog-update>", texts[-1])
        self.assertIn("Added later", texts[-1])
        messages += [{"role": "assistant", "content": "ok"},
                     {"role": "user", "content": "third"}]
        self.prepare(messages, context)
        self.assertEqual(messages[-1]["content"], "third")

    def test_request_prefix_is_identical_between_calls(self):
        """The serialized request of call k is a prefix of call k+1's."""
        messages = [{"role": "user", "content": "Inspect the repo."}]
        context, system = self.prepare(messages, {})
        first = json.dumps([system, messages])[:-2]
        messages += [{"role": "assistant", "content": "reading"},
                     {"role": "user", "content": "result"}]
        self.recall["text"] = '[{"source": "style.md", "content": "Use black."}]'
        context, system = self.prepare(messages, context)
        second = json.dumps([system, messages])
        self.assertTrue(second.startswith(first))


if __name__ == "__main__":
    unittest.main()
