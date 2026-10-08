"""Composition root: choose implementations here; business logic uses interfaces."""
from .actions import MockExecutor
from .actions.taskrunner import TaskRunnerExecutor
from .agent import Agent
from .asr import DisabledASR, WhisperASR, XFYunASR
from .llm import MiniMaxClient, MockLLM
from .scene import load_scene
from .observability import JsonlAudit, NullAudit
from .tts import DisabledTTS, MiniMaxTTS


def build_services(settings, *, llm=None, asr=None, executor=None):
    scene = load_scene(settings.scene)
    if executor is None:
        if settings.executor_provider == "taskrunner":
            if settings.scene != "kaanh_agent":
                raise ValueError("EXECUTOR_PROVIDER=taskrunner requires SCENE=kaanh_agent")
            executor = TaskRunnerExecutor(settings.taskrunner_base_url,
                                          settings.taskrunner_timeout_seconds)
        else:
            executor = MockExecutor()
    provider = llm if llm is not None else (
        MiniMaxClient(settings) if settings.llm_provider == "minimax" else MockLLM()
    )
    if asr is not None:
        transcriber = asr
    elif settings.asr_provider == "whisper":
        transcriber = WhisperASR(settings)
    elif settings.asr_provider == "xfyun":
        transcriber = XFYunASR(settings)
    else:
        transcriber = DisabledASR()
    audit = JsonlAudit(
        settings.log_file, max_bytes=settings.log_max_bytes, backups=settings.log_backup_count,
        include_text=settings.log_include_text,
        secrets=(settings.minimax_api_key.get_secret_value(),
                 settings.porcupine_access_key.get_secret_value(),
                 settings.xfyun_app_id.get_secret_value(),
                 settings.xfyun_api_key.get_secret_value(),
                 settings.xfyun_api_secret.get_secret_value()),
    ) if settings.log_enabled else NullAudit()
    agent = Agent(provider, scene, settings,
                  executor=executor, audit=audit)
    audit.record("service.created", scene=settings.scene, llm_provider=settings.llm_provider,
                 asr_provider=settings.asr_provider, tts_provider=settings.tts_provider,
                 executor=type(agent.executor).__name__)
    return agent, provider, transcriber


def build_tts(settings, *, tts=None):
    if tts is not None:
        return tts
    return MiniMaxTTS(settings) if settings.tts_provider == "minimax" else DisabledTTS()
