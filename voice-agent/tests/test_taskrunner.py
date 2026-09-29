import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from voice_agent.actions.taskrunner import TaskRunnerExecutor
from voice_agent.bootstrap import build_services
from voice_agent.config import Settings
from voice_agent.main import create_app
from voice_agent.schemas import Action

from .test_core import ScriptedLLM, decision, make_agent


def order(item_id="cola"):
    return {"type": "submit_order", "parameters": {"item_id": item_id}}


def receipt(item_id="cola"):
    return {"order_id": "order-123", "item_id": item_id, "target_id": "coco_cola",
            "status": "queued", "tasks": [], "error_code": None, "message": None,
            "created_at": 1.0, "updated_at": 1.0}


@pytest.mark.parametrize("item_id", ["water", "cola", "oolong_tea"])
async def test_order_posts_once_and_returns_backend_receipt(item_id):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "http://runner:9090/api/orders"
        assert json.loads(request.content) == {"item_id": item_id}
        return httpx.Response(201, json=receipt(item_id))

    executor = TaskRunnerExecutor("http://runner:9090", transport=httpx.MockTransport(handle))
    agent = make_agent(ScriptedLLM([decision([order(item_id)])]),
                       scene="kaanh_agent", executor=executor)
    result = await agent.handle("来一瓶饮料")
    assert len(requests) == 1
    assert result.status == "success"
    assert result.action_results[0].status == "accepted"
    assert result.action_results[0].details["order_id"] == "order-123"
    assert result.action_results[0].details["order_status"] == "queued"
    assert "已受理" in result.reply
    assert "模拟" not in result.reply


@pytest.mark.parametrize("outcome", ["timeout", "500", "malformed", "mismatch", "redirect"])
async def test_uncertain_submission_never_retries_or_claims_success(outcome):
    calls = []

    def handle(request):
        calls.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private error", request=request)
        if outcome == "500":
            return httpx.Response(500)
        if outcome == "redirect":
            return httpx.Response(307, headers={"Location": "/api/orders"})
        return httpx.Response(201, json=receipt("water") if outcome == "mismatch" else {})

    executor = TaskRunnerExecutor("http://runner", transport=httpx.MockTransport(handle))
    result = await make_agent(ScriptedLLM([decision([order()])]),
                              scene="kaanh_agent", executor=executor).handle("可乐")
    assert len(calls) == 1
    assert result.status == "failed"
    assert result.action_results[0].status == "unknown"
    assert "核对" in result.reply
    assert "private" not in result.model_dump_json()


async def test_backend_rejection_is_reported_without_retry():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(409, json={"error": {"type": "QueueFullError",
                                                  "message": "private"}})

    executor = TaskRunnerExecutor("http://runner", transport=httpx.MockTransport(handle))
    result = await make_agent(ScriptedLLM([decision([order()])]),
                              scene="kaanh_agent", executor=executor).handle("可乐")
    assert len(calls) == 1
    assert result.status == "failed"
    assert result.action_results[0].status == "failed"
    assert "未受理" in result.reply
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("actions", [[order("coffee")], [order(), order()],
                                     [order(), {"type": "stop", "parameters": {}}]])
async def test_invalid_or_multiple_orders_never_reach_backend(actions):
    calls = []
    executor = TaskRunnerExecutor("http://runner", transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(201, json=receipt())))
    raw = decision(actions)
    result = await make_agent(ScriptedLLM([raw, raw]), scene="kaanh_agent",
                              executor=executor).handle("test")
    assert result.status == "error"
    assert calls == []


async def test_executor_rejects_non_order_action_without_http():
    calls = []
    executor = TaskRunnerExecutor("http://runner", transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(201, json=receipt())))
    with pytest.raises(ValueError):
        await executor.execute(Action(type="stop"))
    assert calls == []


def test_bootstrap_requires_explicit_provider_and_order_scene():
    agent, _, _ = build_services(Settings(_env_file=None, log_enabled=False))
    assert type(agent.executor).__name__ == "MockExecutor"
    agent, _, _ = build_services(Settings(_env_file=None, log_enabled=False,
                                         executor_provider="taskrunner", scene="kaanh_agent"))
    assert isinstance(agent.executor, TaskRunnerExecutor)
    with pytest.raises(ValueError):
        build_services(Settings(_env_file=None, log_enabled=False, executor_provider="taskrunner"))


def test_audio_to_order_over_http_and_followup_does_not_resubmit():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(
                int(self.headers["Content-Length"])))))
            payload = json.dumps(receipt()).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class ASR:
        async def transcribe(self, audio):
            assert audio == b"recording"
            return "给我一瓶可乐"

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(_env_file=None, log_enabled=False, scene="kaanh_agent",
                            executor_provider="taskrunner",
                            taskrunner_base_url=f"http://127.0.0.1:{server.server_port}")
        with TestClient(create_app(settings, asr=ASR(), llm=ScriptedLLM(
                [decision([order()]), decision(reply="不客气。")])) ) as client:
            body = client.post("/audio", files={"file": ("voice.wav", b"recording")}).json()
            assert body["transcript"] == "给我一瓶可乐"
            assert body["action_results"][0]["details"]["order_id"] == "order-123"
            assert body["action_results"][0]["status"] == "accepted"
            followup = client.post("/chat", json={"text": "谢谢", "session_id": body["session_id"]})
            assert followup.json()["actions"] == []
            assert client.get("/health").json()["executor"] == "taskrunner"
        assert requests == [("/api/orders", {"item_id": "cola"})]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_order_scene_with_default_executor_still_only_simulates():
    agent, _, _ = build_services(Settings(_env_file=None, log_enabled=False,
                                         scene="kaanh_agent"),
                                  llm=ScriptedLLM([decision([order()])]))
    response = await agent.handle("可乐")
    assert response.action_results[0].status == "simulated"
    assert "模拟" in response.reply
