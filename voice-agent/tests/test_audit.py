import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from voice_agent.observability import JsonlAudit

from .test_core import ScriptedLLM, decision, make_agent, move


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class AuditFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nested" / "agent.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def audit(self, **options):
        return JsonlAudit(self.path, **options)

    async def test_action_lifecycle_is_persisted_with_same_id(self):
        audit = self.audit()
        agent = make_agent(ScriptedLLM([decision([move()])]))
        agent.audit = audit
        result = await agent.handle("去厨房")
        audit.close()
        data = rows(self.path)
        names = [row["event"] for row in data]
        self.assertIn("plan.created", names)
        self.assertFalse(any(name.startswith("confirmation.") for name in names))
        self.assertIn("action.started", names)
        self.assertIn("action.finished", names)
        action_id = result.actions[0].action_id
        self.assertEqual({row["action_id"] for row in data if row["event"].startswith("action.")},
                         {action_id})
        self.assertTrue(all(row["run_id"] for row in data))
        self.assertEqual([row["sequence"] for row in data], list(range(1, len(data) + 1)))

    async def test_rejected_request_never_has_action_started(self):
        audit = self.audit()
        agent = make_agent(ScriptedLLM([decision(status="rejected", reply="该请求不合理，无法执行。")]))
        agent.audit = audit
        result = await agent.handle("test")
        audit.close()
        names = [row["event"] for row in rows(self.path)]
        self.assertEqual(result.status, "rejected")
        self.assertNotIn("action.started", names)

    async def test_concurrent_actions_produce_valid_complete_lines(self):
        audit = self.audit()

        async def write(index):
            await asyncio.to_thread(audit.record, "test", index=index)

        await asyncio.gather(*(write(index) for index in range(100)))
        audit.close()
        data = rows(self.path)
        self.assertEqual(len(data), 100)
        self.assertEqual({row["index"] for row in data}, set(range(100)))

    def test_secrets_are_redacted_recursively(self):
        audit = self.audit(secrets=("known-value",))
        audit.record("test", text="contains known-value", api_key="top-secret",
                     nested={"authorization": "Bearer value", "safe": "known-value"})
        audit.close()
        row = rows(self.path)[0]
        self.assertEqual(row["api_key"], "[REDACTED]")
        self.assertEqual(row["nested"]["authorization"], "[REDACTED]")
        self.assertNotIn("known-value", json.dumps(row))

    def test_text_can_be_excluded(self):
        audit = self.audit(include_text=False)
        audit.record("response", text="spoken command",
                     response={"reply": "answer", "intent": "control", "actions": []})
        audit.close()
        row = rows(self.path)[0]
        self.assertNotIn("text", row)
        self.assertNotIn("reply", row["response"])
        self.assertEqual(row["response"]["intent"], "control")

    def test_rotation_keeps_bounded_number_of_files(self):
        audit = self.audit(max_bytes=350, backups=2)
        for index in range(30):
            audit.record("large", index=index, value="x" * 100)
        audit.close()
        files = sorted(self.path.parent.glob("agent.jsonl*"))
        self.assertGreater(len(files), 1)
        self.assertLessEqual(len(files), 3)
        for file in files:
            rows(file)

    def test_invalid_rotation_settings(self):
        with self.assertRaises(ValueError):
            JsonlAudit(self.path, max_bytes=0)

    def test_reopening_appends_instead_of_erasing_history(self):
        first = self.audit()
        first.record("before_restart")
        first.close()
        second = self.audit()
        second.record("after_restart")
        second.close()
        data = rows(self.path)
        self.assertEqual([row["event"] for row in data], ["before_restart", "after_restart"])
        self.assertNotEqual(data[0]["run_id"], data[1]["run_id"])

    def test_disk_error_warns_without_throwing_to_action_caller(self):
        audit = self.audit()
        with patch.object(audit.handler, "handle", side_effect=OSError("disk full")):
            with self.assertLogs("voice_agent.observability.audit", level="WARNING") as warning:
                audit.record("action.finished", action_id="test")
                audit.record("response")
        audit.close()
        self.assertEqual(len(warning.output), 1)


if __name__ == "__main__":
    unittest.main()
