import json
import time
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from voice_agent.actions import MockExecutor
from voice_agent.agent import Agent
from voice_agent.llm import MockLLM, ProviderError
from voice_agent.scene import load_scene
from voice_agent.schemas import AgentDecision


def decision(actions=None, **changes):
    value = dict(reply="准备执行。", intent="test", actions=actions or [],
                 status="ready")
    value.update(changes)
    return json.dumps(value, ensure_ascii=False)


def move(location="kitchen"):
    return {"type": "move_to", "parameters": {"location": location}}


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def complete(self, messages):
        self.calls.append(list(messages))
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self):
        pass


class CountingExecutor(MockExecutor):
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def execute(self, action):
        self.calls.append(action)
        if self.fail:
            raise RuntimeError("Device fault")
        return await super().execute(action)


def make_agent(llm=None, scene="robot_demo", executor=None):
    # Pure core depends only on configuration values, not the environment loader.
    config = SimpleNamespace(max_sessions=10, session_ttl_seconds=3600)
    return Agent(llm or MockLLM(), load_scene(scene), config, executor)


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_light_output_and_simulation(self):
        result = await make_agent(scene="home_assistant").handle("打开客厅的灯")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.actions[0].parameters, {"room": "living_room", "state": "on"})
        self.assertEqual(result.action_results[0].status, "simulated")
        self.assertIn("未控制真实设备", result.reply)

    async def test_ambiguous_request_has_no_actions(self):
        result = await make_agent(scene="home_assistant").handle("打开灯")
        self.assertEqual(result.status, "waiting_for_user")
        self.assertEqual(result.actions, [])

    async def test_allowed_actions_execute_immediately_once_in_order(self):
        executor = CountingExecutor()
        actions = [move(), {"type": "pick_object", "parameters": {"object": "cup"}}, move("user")]
        actions[0]["action_id"] = "untrusted-model-id"
        agent = make_agent(ScriptedLLM([decision(actions)]), executor=executor)
        result = await agent.handle("把厨房的杯子拿过来")
        self.assertEqual(result.status, "success")
        self.assertEqual(len(executor.calls), 3)
        self.assertEqual([a.type for a in executor.calls], ["move_to", "pick_object", "move_to"])
        self.assertNotEqual(result.actions[0].action_id, "untrusted-model-id")
        self.assertEqual([a.action_id for a in executor.calls],
                         [r.action_id for r in result.action_results])

    async def test_subsequent_chat_does_not_replay_last_action(self):
        executor = CountingExecutor()
        agent = make_agent(ScriptedLLM([decision([move()]), decision()]), executor=executor)
        result = await agent.handle("去厨房")
        await agent.handle("你好", result.session_id)
        self.assertEqual(len(executor.calls), 1)

    async def test_rejection_preserves_reason_and_does_not_execute(self):
        executor = CountingExecutor()
        reason = "当前场景不支持抓取书本，无法执行。"
        agent = make_agent(ScriptedLLM([decision(status="rejected", reply=reason)]),
                           executor=executor)
        result = await agent.handle("帮我抓取书本")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reply, reason)
        self.assertEqual(result.actions, [])
        self.assertEqual(result.action_results, [])
        self.assertEqual(executor.calls, [])

    async def test_expired_session_does_not_execute(self):
        executor = CountingExecutor()
        agent = make_agent(ScriptedLLM([decision()]), executor=executor)
        previous = await agent.handle("你好")
        agent.sessions.get(previous.session_id).touched = time.monotonic() - 4000
        result = await agent.handle("去厨房", previous.session_id)
        self.assertEqual(result.status, "error")
        self.assertEqual(executor.calls, [])

    async def test_rejected_decision_cannot_smuggle_allowed_action(self):
        executor = CountingExecutor()
        raw = decision([move()], status="rejected", reply="请求不合理，无法执行。")
        agent = make_agent(ScriptedLLM([raw, raw]), executor=executor)
        result = await agent.handle("test")
        self.assertEqual(result.status, "error")
        self.assertEqual(executor.calls, [])

    async def test_invalid_action_prevents_entire_batch(self):
        executor = CountingExecutor()
        invalid = decision([{"type": "stop", "parameters": {}},
                            {"type": "shell", "parameters": {"command": "do something"}}])
        agent = make_agent(ScriptedLLM([invalid, invalid]), executor=executor)
        result = await agent.handle("test")
        self.assertEqual(result.status, "error")
        self.assertEqual(executor.calls, [])

    async def test_invalid_parameters_are_rejected(self):
        invalid = decision([move("unknown_place")])
        agent = make_agent(ScriptedLLM([invalid, invalid]))
        result = await agent.handle("test")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.actions, [])

    async def test_one_repair_attempt_then_error(self):
        llm = ScriptedLLM(["```json {} ```", "not json"])
        result = await make_agent(llm).handle("test")
        self.assertEqual(result.status, "error")
        self.assertEqual(len(llm.calls), 2)

    async def test_valid_repair_is_accepted(self):
        llm = ScriptedLLM(["bad json", decision()])
        result = await make_agent(llm).handle("test")
        self.assertEqual(result.status, "success")

    async def test_provider_error_does_not_leak_details(self):
        result = await make_agent(ScriptedLLM([ProviderError("private body")])).handle("test")
        self.assertEqual(result.status, "error")
        self.assertNotIn("private body", result.model_dump_json())

    async def test_sessions_are_isolated(self):
        llm = ScriptedLLM([decision(), decision(), decision()])
        agent = make_agent(llm)
        first = await agent.handle("first session secret")
        await agent.handle("second session")
        self.assertNotIn("first session secret", str(llm.calls[1]))
        await agent.handle("follow up", first.session_id)
        self.assertIn("first session secret", str(llm.calls[2]))

    async def test_executor_failure_stops_following_actions(self):
        executor = CountingExecutor(fail=True)
        agent = make_agent(ScriptedLLM([decision([move(), move("user")])]), executor=executor)
        result = await agent.handle("test")
        self.assertEqual(result.status, "failed")
        self.assertEqual([r.status for r in result.action_results], ["failed", "skipped"])
        self.assertEqual(len(executor.calls), 1)

    async def test_registered_action_outside_scene_does_not_execute(self):
        executor = CountingExecutor()
        raw = decision([move()])
        agent = make_agent(ScriptedLLM([raw, raw]), scene="home_assistant", executor=executor)
        result = await agent.handle("去厨房")
        self.assertEqual(result.status, "error")
        self.assertIn("当前场景不支持", result.reply)
        self.assertEqual(executor.calls, [])

    def test_strict_schema_rejects_legacy_confirmation_and_extra_fields(self):
        for raw in [decision(need_confirmation=False), decision(status="awaiting_confirmation"),
                    decision(unexpected=True)]:
            with self.assertRaises(ValidationError):
                AgentDecision.model_validate_json(raw)

    def test_clarification_cannot_include_actions(self):
        with self.assertRaises(ValidationError):
            AgentDecision.model_validate_json(decision([move()], status="waiting_for_user"))

    def test_scene_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            load_scene("../../secret")


if __name__ == "__main__":
    unittest.main()
