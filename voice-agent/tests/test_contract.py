import asyncio
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from voice_agent.scene import build_prompt, load_scene
from voice_agent.schemas import AgentDecision, AgentResponse

from .test_core import CountingExecutor, ScriptedLLM, decision, make_agent, move


class DirectExecutionContractTests(unittest.IsolatedAsyncioTestCase):
    def test_exported_and_prompt_schemas_match_python_without_confirmation(self):
        root = Path(__file__).resolve().parents[1]
        for filename, model in [("agent-decision.schema.json", AgentDecision),
                                ("agent-response.schema.json", AgentResponse)]:
            schema = model.model_json_schema()
            self.assertEqual(json.loads((root / "schemas" / filename).read_text()), schema)
            self.assertNotIn("confirmation", json.dumps(schema))
            self.assertIn("rejected", schema["properties"]["status"]["enum"])
        for scene_name in ("home_assistant", "robot_demo"):
            prompt = build_prompt(load_scene(scene_name))
            raw = next(line.split("=", 1)[1] for line in prompt.splitlines()
                       if line.startswith("DECISION_SCHEMA="))
            self.assertEqual(json.loads(raw), AgentDecision.model_json_schema())
            self.assertNotIn("confirmation", prompt)

    def test_rejection_requires_nonblank_reason(self):
        with self.assertRaises(ValidationError):
            AgentDecision.model_validate_json(decision(status="rejected", reply="   "))

    async def test_legacy_confirmation_output_never_executes(self):
        executor = CountingExecutor()
        legacy = decision([move()], need_confirmation=True, status="awaiting_confirmation")
        agent = make_agent(ScriptedLLM([legacy, legacy]), executor=executor)
        result = await agent.handle("去厨房")
        self.assertEqual(result.status, "error")
        self.assertEqual(executor.calls, [])

    async def test_mock_rejects_action_not_available_in_scene(self):
        executor = CountingExecutor()
        result = await make_agent(scene="home_assistant", executor=executor).handle("去厨房")
        self.assertEqual(result.status, "rejected")
        self.assertIn("不支持", result.reply)
        self.assertEqual(executor.calls, [])

    async def test_unreasonable_request_rejection_survives_next_turn(self):
        reason = "这个用途违反当前场景规则，无法执行。"
        llm = ScriptedLLM([decision(status="rejected", intent="refusal", reply=reason), decision()])
        executor = CountingExecutor()
        agent = make_agent(llm, executor=executor)
        first = await agent.handle("违反场景规则的请求")
        await agent.handle("为什么？", first.session_id)
        prior = next(m["content"] for m in llm.calls[-1] if m["role"] == "assistant")
        self.assertEqual(AgentDecision.model_validate_json(prior).reply, reason)
        self.assertEqual(executor.calls, [])

    async def test_concurrent_requests_in_same_session_execute_serially(self):
        class SerialExecutor(CountingExecutor):
            busy = False

            async def execute(self, action):
                if self.busy:
                    raise RuntimeError("overlapping action execution")
                self.busy = True
                try:
                    await asyncio.sleep(0)
                    return await super().execute(action)
                finally:
                    self.busy = False

        executor = SerialExecutor()
        llm = ScriptedLLM([decision(), decision([move()]), decision([move("user")])])
        agent = make_agent(llm, executor=executor)
        first = await agent.handle("你好")
        results = await asyncio.gather(agent.handle("去厨房", first.session_id),
                                       agent.handle("回来", first.session_id))
        self.assertTrue(all(result.status == "success" for result in results))
        self.assertEqual(len(executor.calls), 2)


if __name__ == "__main__":
    unittest.main()
