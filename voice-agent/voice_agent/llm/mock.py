import json

from ..schemas import Action, AgentDecision


class MockLLM:
    """Small deterministic demo; it is not a language model."""

    async def complete(self, messages: list[dict[str, str]]) -> str:
        text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        text = text.strip()
        actions = []
        reply, intent, status = "你好！这是模拟模式，可以试试：打开客厅的灯。", "chat", "ready"
        if "停止" in text or text.lower() == "stop":
            actions = [Action(type="stop")]
            reply, intent = "准备模拟停止。", "stop"
        elif "灯" in text:
            room = next((v for k, v in {"客厅": "living_room", "卧室": "bedroom",
                                      "厨房": "kitchen"}.items() if k in text), None)
            state = "off" if "关" in text else "on" if "开" in text else None
            if room and state:
                actions = [Action(type="set_light", parameters={"room": room, "state": state})]
                reply, intent = "准备模拟调整灯光。", "control_light"
            else:
                reply, intent, status = "请明确房间，以及开灯还是关灯。", "clarification", "waiting_for_user"
        elif "厨房" in text and "去" in text:
            actions = [Action(type="move_to", parameters={"location": "kitchen"})]
            reply, intent = "准备模拟移动到厨房。", "robot_control"
        # Match the scene's action scope in deterministic demos too.
        scene_line = next((line for message in messages if message["role"] == "system"
                           for line in message["content"].splitlines()
                           if line.startswith("SCENE=")), None)
        if scene_line is not None:
            allowed = json.loads(scene_line.removeprefix("SCENE="))["available_actions"]
            if any(action.type not in allowed for action in actions):
                actions = []
                reply = "当前场景不支持这个动作，无法执行该请求。"
                intent, status = "unsupported_request", "rejected"
        return AgentDecision(reply=reply, intent=intent, actions=actions,
                             status=status).model_dump_json()

    async def close(self):
        pass
