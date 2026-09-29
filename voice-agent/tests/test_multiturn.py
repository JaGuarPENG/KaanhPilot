import json
import tempfile
import unittest
from pathlib import Path

from voice_agent.observability import JsonlAudit
from voice_agent.runtime.conversation import LocalConversation
from voice_agent.schemas import AgentDecision

from .test_core import CountingExecutor, ScriptedLLM, decision, make_agent, move


class MultiTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_clarification_then_object_executes_without_extra_approval(self):
        llm = ScriptedLLM([
            decision(reply="你要搬运哪个物品？", intent="clarification", status="waiting_for_user"),
            decision([{"type": "pick_object", "parameters": {"object": "cup"}}]),
        ])
        executor = CountingExecutor()
        agent = make_agent(llm, executor=executor)
        conversation = LocalConversation(agent)
        question = await conversation.handle("帮我搬运物品")
        result = await conversation.handle("杯子")
        self.assertEqual(question.session_id, result.session_id)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0].parameters, {"object": "cup"})
        messages = llm.calls[1]
        prior = next(m["content"] for m in messages if m["role"] == "assistant")
        parsed = AgentDecision.model_validate_json(prior)
        self.assertEqual(parsed.reply, "你要搬运哪个物品？")
        self.assertNotIn("session_id", json.loads(prior))
        self.assertIn("帮我搬运物品", str(messages))
        self.assertEqual(messages[-1], {"role": "user", "content": "杯子"})

    async def test_execution_outcomes_are_observations_not_assistant_response_envelopes(self):
        for fail, status in [(False, "success"), (True, "failed")]:
            with self.subTest(fail=fail):
                executor = CountingExecutor(fail=fail)
                llm = ScriptedLLM([decision([move()]), decision()])
                agent = make_agent(llm, executor=executor)
                result = await agent.handle("去厨房")
                await agent.handle("现在怎么样？", result.session_id)
                self.assertEqual(result.status, status)
                messages = llm.calls[-1]
                assistants = [AgentDecision.model_validate_json(m["content"])
                              for m in messages if m["role"] == "assistant"]
                self.assertEqual(assistants[-1].status, "ready")
                observations = [json.loads(m["content"].split("SERVER_OBSERVATION=", 1)[1])
                                for m in messages if "SERVER_OBSERVATION=" in m["content"]]
                self.assertEqual(observations[-1]["status"], status)
                self.assertEqual(observations[-1]["action_results"],
                                 [item.model_dump() for item in result.action_results])
                self.assertEqual(len(executor.calls), 1)

    async def test_repair_receives_rejected_candidate_and_errors_without_persisting_candidate(self):
        invalid = decision(status="success", session_id="server-only-id")
        llm = ScriptedLLM([invalid, decision(), decision()])
        agent = make_agent(llm)
        result = await agent.handle("你好")
        self.assertEqual(result.status, "success")
        retry = llm.calls[1]
        self.assertEqual(retry[-2], {"role": "assistant", "content": invalid})
        errors = json.loads(retry[-1]["content"].split("VALIDATION_ERRORS=", 1)[1])
        self.assertIn("extra_forbidden", {item["type"] for item in errors})
        self.assertIn("literal_error", {item["type"] for item in errors})
        await agent.handle("继续", result.session_id)
        self.assertNotIn("server-only-id", str(llm.calls[2]))
        for message in llm.calls[2]:
            if message["role"] == "assistant":
                AgentDecision.model_validate_json(message["content"])

    async def test_validation_logs_classify_failure_without_raw_model_reply(self):
        cases = [
            ("json_syntax", "not-json PRIVATE-REPLY"),
            ("decision_schema", decision(reply="PRIVATE-REPLY", status="success")),
            ("action_parameters", decision([move("PRIVATE-REPLY")])),
            ("unsupported_action", decision([{"type": "PRIVATE-REPLY", "parameters": {}}])),
        ]
        for kind, raw in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "agent.jsonl"
                audit = JsonlAudit(path, include_text=False)
                executor = CountingExecutor()
                agent = make_agent(ScriptedLLM([raw, raw]), executor=executor)
                agent.audit = audit
                try:
                    result = await agent.handle("test")
                finally:
                    audit.close()
                self.assertEqual(result.status, "error")
                self.assertEqual(executor.calls, [])
                data = path.read_text(encoding="utf-8")
                errors = [row for row in map(json.loads, data.splitlines())
                          if row["event"] == "model.validation_failed"]
                self.assertEqual([row["attempt"] for row in errors], [1, 2])
                self.assertEqual({row["failure_kind"] for row in errors}, {kind})
                self.assertTrue(all(row["validation_errors"] for row in errors))
                self.assertNotIn("PRIVATE-REPLY", data)

    async def test_history_retains_ten_complete_turns(self):
        agent = make_agent(ScriptedLLM([decision()] * 12))
        session_id = None
        for index in range(12):
            response = await agent.handle(f"turn {index}", session_id)
            session_id = response.session_id
        history = agent.sessions.get(session_id).history
        self.assertEqual(len(history), 30)
        self.assertEqual(history[0], {"role": "user", "content": "turn 2"})
        for offset in range(0, 30, 3):
            self.assertEqual([m["role"] for m in history[offset:offset + 3]],
                             ["user", "assistant", "user"])
            AgentDecision.model_validate_json(history[offset + 1]["content"])

    async def test_unsupported_object_after_clarification_never_executes(self):
        invalid = decision([{"type": "pick_object", "parameters": {"object": "book"}}])
        llm = ScriptedLLM([decision(status="waiting_for_user"), invalid, invalid])
        executor = CountingExecutor()
        agent = make_agent(llm, executor=executor)
        first = await agent.handle("搬运那个物品")
        result = await agent.handle("书", first.session_id)
        self.assertEqual(result.status, "error")
        self.assertEqual(executor.calls, [])
        self.assertIn("literal_error", llm.calls[-1][-1]["content"])


if __name__ == "__main__":
    unittest.main()
