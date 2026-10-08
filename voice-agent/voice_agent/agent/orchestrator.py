from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError

from ..actions import MockExecutor, validate_actions
from ..llm import LLMProvider, ProviderError
from ..memory import SessionStore
from ..scene import Scene, build_prompt
from ..schemas import ActionResult, AgentDecision, AgentResponse
from ..observability import AuditSink, NullAudit

if TYPE_CHECKING:
    from ..config import Settings


class Agent:
    def __init__(self, llm: LLMProvider, scene: Scene, settings: Settings, executor=None,
                 audit: AuditSink | None = None):
        self.llm, self.scene, self.settings = llm, scene, settings
        self.executor = executor or MockExecutor()
        self.sessions = SessionStore(settings.max_sessions, settings.session_ttl_seconds)
        self.audit = audit if audit is not None else NullAudit()

    @staticmethod
    def error(reply: str, session_id: str | None = None) -> AgentResponse:
        return AgentResponse(reply=reply, intent="error", status="error", session_id=session_id)

    async def handle(self, text: str, session_id: str | None = None) -> AgentResponse:
        request_id = uuid4().hex
        self.audit.record("request.received", request_id=request_id, session_id=session_id,
                          text=text, scene=self.scene.name)
        try:
            result = await self._handle(text, session_id, request_id)
        except Exception as exc:
            self.audit.record("request.error", request_id=request_id, session_id=session_id,
                              error_type=type(exc).__name__)
            raise
        self.audit.record("response", request_id=request_id, session_id=result.session_id,
                          status=result.status, response=result.model_dump())
        return result

    async def _handle(self, text: str, session_id: str | None, request_id: str) -> AgentResponse:
        try:
            session = self.sessions.get(session_id)
        except ValueError as exc:
            return self.error(str(exc), session_id)
        async with session.lock:
            self.audit.record("request.session", request_id=request_id, session_id=session.id)
            messages = [{"role": "system", "content": build_prompt(self.scene)},
                        *session.history, {"role": "user", "content": text}]
            decision = None
            for attempt in range(2):
                try:
                    raw = await self.llm.complete(messages)
                    validation_stage = "decision"
                    decision = AgentDecision.model_validate_json(raw)
                    validation_stage = "actions"
                    validate_actions(decision.actions, self.scene.available_actions)
                    break
                except ProviderError:
                    self.audit.record("model.error", request_id=request_id, session_id=session.id)
                    return self.error("模型服务暂时不可用，请检查配置后重试。", session.id)
                except (ValidationError, ValueError) as exc:
                    if isinstance(exc, ValidationError):
                        errors = exc.errors(include_input=False, include_context=False,
                                            include_url=False)[:8]
                        failure_kind = (
                            "json_syntax" if any(e["type"] == "json_invalid" for e in errors)
                            else "action_parameters" if validation_stage == "actions"
                            else "decision_schema"
                        )
                    else:
                        errors = [{"type": "unsupported_action", "loc": ["actions"],
                                   "msg": "Action is not registered or allowed in this scene."}]
                        failure_kind = "unsupported_action"
                    self.audit.record("model.validation_failed", request_id=request_id,
                                      session_id=session.id, attempt=attempt + 1,
                                      failure_kind=failure_kind, validation_errors=errors)
                    if attempt:
                        reason = {
                            "json_syntax": "模型没有返回合法 JSON，未执行动作。",
                            "decision_schema": "模型输出字段或状态不符合协议，未执行动作。",
                            "action_parameters": "模型选择的动作参数不受支持或不完整，未执行动作。",
                            "unsupported_action": "模型选择的动作未注册或当前场景不支持，未执行动作。",
                        }[failure_kind]
                        return self.error(reason, session.id)
                    # This failed candidate stays in the repair request only, not session history.
                    messages.extend([{"role": "assistant", "content": raw},
                                     {"role": "user", "content":
                        "Your previous candidate was rejected; no actions were executed. "
                        "Return one corrected AgentDecision JSON object for the original user "
                        "request, using the clarification context. Do not add Markdown or server "
                        "response fields. Correct the following validation errors while preserving "
                        "the user's intent; if required parameters remain unclear, ask a question "
                        "with no actions. If the request is unsupported or unreasonable, return "
                        "status=rejected, actions=[], and explain why in reply.\nVALIDATION_ERRORS="
                        + json.dumps(errors, ensure_ascii=False)}])
            assert decision is not None
            # IDs are always assigned by the server, never trusted from the model.
            for action in decision.actions:
                action.action_id = uuid4().hex
            self.audit.record("plan.created", request_id=request_id, session_id=session.id,
                              intent=decision.intent,
                              actions=[a.model_dump() for a in decision.actions])
            if decision.status in {"waiting_for_user", "rejected"}:
                result = AgentResponse(reply=decision.reply, intent=decision.intent,
                                       status=decision.status, session_id=session.id)
            else:
                result = await self.execute(decision, session.id, request_id)
            session.remember(text, decision, result)
            return result

    async def execute(self, decision: AgentDecision, session_id: str,
                      request_id: str | None = None) -> AgentResponse:
        validate_actions(decision.actions, self.scene.available_actions)
        results = []
        failed = False
        for action in decision.actions:
            if failed:
                item = ActionResult(action_id=action.action_id, type=action.type, status="skipped")
            else:
                self.audit.record("action.started", request_id=request_id, session_id=session_id,
                                  **action.model_dump())
                try:
                    item = await self.executor.execute(action)
                except Exception:
                    item = ActionResult(action_id=action.action_id, type=action.type,
                                        status="failed", details={"error": "executor_failed"})
                failed = item.status in {"failed", "unknown"}
            results.append(item)
            self.audit.record("action.finished", request_id=request_id, session_id=session_id,
                              **item.model_dump())
        reply = decision.reply
        if results:
            if any(item.status == "unknown" for item in results):
                reply = "订单提交结果待核对，可能已经入队。请先核对商品页面的队列，不要重复下单。"
            elif any(item.status == "accepted" for item in results):
                reply = "订单已受理，请在商品页面查看执行进度。"
            elif any(item.details.get("executor") == "taskrunner" for item in results):
                reply = "订单未受理，请检查后端服务状态或队列容量。"
            else:
                reply = "模拟执行失败，后续动作已停止。" if failed else "模拟动作已完成，未控制真实设备。"
        return AgentResponse(reply=reply, intent=decision.intent, actions=decision.actions,
                             action_results=results, status="failed" if failed else "success",
                             session_id=session_id)
