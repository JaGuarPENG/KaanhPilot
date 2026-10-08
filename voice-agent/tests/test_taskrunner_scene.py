import pytest

from voice_agent.actions.taskrunner import TaskRunnerExecutor
from voice_agent.bootstrap import build_services
from voice_agent.config import Settings
from voice_agent.scene import load_scene


def test_custom_order_scene_can_use_taskrunner():
    agent, _, _ = build_services(Settings(
        _env_file=None, log_enabled=False,
        executor_provider="taskrunner", scene="kaanh_agent",
    ))
    assert isinstance(agent.executor, TaskRunnerExecutor)
    assert agent.scene.name == load_scene("kaanh_agent").name


def test_taskrunner_rejects_incompatible_actions():
    with pytest.raises(ValueError, match="submit_order"):
        build_services(Settings(
            _env_file=None, log_enabled=False,
            executor_provider="taskrunner", scene="home_assistant",
        ))


def test_taskrunner_rejects_mixed_order_and_device_actions(monkeypatch):
    scene = load_scene("kaanh_agent").model_copy(
        update={"available_actions": ["submit_order", "stop"]},
    )
    monkeypatch.setattr("voice_agent.bootstrap.load_scene", lambda name: scene)
    with pytest.raises(ValueError, match="submit_order"):
        build_services(Settings(
            _env_file=None, log_enabled=False,
            executor_provider="taskrunner", scene="kaanh_agent",
        ))
