from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["mock", "minimax"] = "mock"
    executor_provider: Literal["mock", "taskrunner"] = "mock"
    taskrunner_base_url: str = "http://127.0.0.1:9090"
    taskrunner_timeout_seconds: float = Field(default=8, gt=0, le=60)
    minimax_api_key: SecretStr = SecretStr("")
    minimax_base_url: str = "https://api.minimax.cn/v1"
    minimax_model: str = "MiniMax-M3"
    minimax_thinking: Literal["disabled", "adaptive"] = "disabled"
    model_timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_completion_tokens: int = Field(default=4096, ge=256, le=32768)
    tts_provider: Literal["disabled", "minimax"] = "disabled"
    tts_streaming: bool = True
    tts_base_url: str = ""
    tts_model: str = "speech-2.8-turbo"
    tts_voice_id: str = "Chinese (Mandarin)_Gentleman"
    tts_language_boost: str = "Chinese"
    tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    tts_volume: float = Field(default=1.0, ge=0.1, le=10.0)
    tts_pitch: int = Field(default=0, ge=-12, le=12)
    tts_sample_rate: int = Field(default=32000, ge=8000, le=48000)
    tts_bitrate: int = Field(default=128000, ge=32000, le=320000)
    tts_timeout_seconds: float = Field(default=60, gt=0, le=300)
    tts_max_audio_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    asr_provider: Literal["disabled", "whisper", "xfyun"] = "disabled"
    xfyun_asr_streaming: bool = True
    whisper_model: str = "small"
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "zh"
    xfyun_app_id: SecretStr = SecretStr("")
    xfyun_api_key: SecretStr = SecretStr("")
    xfyun_api_secret: SecretStr = SecretStr("")
    xfyun_asr_url: str = "wss://iat-api.xfyun.cn/v2/iat"
    xfyun_asr_language: str = "zh_cn"
    xfyun_asr_domain: str = "iat"
    xfyun_asr_accent: str = "mandarin"
    xfyun_asr_dynamic_correction: bool = True
    xfyun_asr_timeout_seconds: float = Field(default=75, gt=0, le=120)
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_audio_seconds: float = Field(default=60, gt=0, le=300)
    scene: str = "home_assistant"
    max_sessions: int = Field(default=200, ge=1)
    session_ttl_seconds: int = Field(default=3600, ge=1)
    wake_provider: Literal["asr", "porcupine", "xfyun"] = "asr"
    xfyun_wake_sdk_dir: str = "voice_agent/resources/xfyun_wake"
    xfyun_wake_keyword_path: str = "voice_agent/resources/xfyun_wake/keyword.txt"
    xfyun_wake_work_dir: str = "logs/xfyun_wake"
    xfyun_wake_threshold: str = Field(default="0 0:999", pattern=r"^[0-9 :|]+$")
    wake_phrase: str = Field(default="你好小助手", min_length=1, max_length=80)
    wake_timeout_seconds: float = Field(default=8, ge=1, le=60)
    wake_sensitivity: float = Field(default=0.5, ge=0, le=1)
    porcupine_access_key: SecretStr = SecretStr("")
    porcupine_keyword_path: str = ""
    porcupine_model_path: str = ""
    vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    audio_frame_ms: int = Field(default=30, ge=10, le=30)
    audio_pre_roll_ms: int = Field(default=300, ge=10)
    audio_start_speech_ms: int = Field(default=60, ge=10)
    audio_min_speech_ms: int = Field(default=240, ge=10)
    audio_end_silence_ms: int = Field(default=800, ge=10, le=5000)
    audio_max_utterance_ms: int = Field(default=15000, ge=1000, le=60000)
    log_enabled: bool = True
    log_file: str = "logs/agent.jsonl"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=100)
    log_include_text: bool = True
