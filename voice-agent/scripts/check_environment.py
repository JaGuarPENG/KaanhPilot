"""Read-only installation/configuration check. Never prints keys or calls remote APIs."""
import argparse
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
import sys
from urllib.parse import urlsplit


CORE = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "httpx": "httpx",
    "pydantic": "pydantic", "pydantic_settings": "pydantic-settings",
    "multipart": "python-multipart", "yaml": "PyYAML", "pytest": "pytest",
    "pytest_asyncio": "pytest-asyncio", "ruff": "ruff",
}
VOICE_COMMON = {
    "webrtcvad": "webrtcvad-wheels", "sounddevice": "sounddevice", "soundfile": "soundfile",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", action="store_true", help="Also check STT/microphone libraries")
    args = parser.parse_args()
    errors = []

    def report(ok, label, detail=""):
        print(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            errors.append(label)

    report(sys.version_info >= (3, 11), "Python", sys.version.split()[0])
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        print("[NOTE] The project CI targets Python 3.11 and 3.12.")
    report(Path("pyproject.toml").is_file(), "Project root", str(Path.cwd()))
    voice_dependencies = dict(VOICE_COMMON) if args.voice else {}
    if args.voice and importlib.util.find_spec("pydantic_settings") is not None:
        try:
            from voice_agent.config import Settings

            selected_asr = Settings().asr_provider
        except Exception:
            selected_asr = None
        if selected_asr == "whisper":
            voice_dependencies["faster_whisper"] = "faster-whisper"
        elif selected_asr == "xfyun":
            voice_dependencies["websockets"] = "websockets"
    for module, distribution in {**CORE, **voice_dependencies}.items():
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(distribution)
        except Exception as exc:
            report(False, distribution, f"Cannot import ({type(exc).__name__}); reinstall requirements")
        else:
            report(True, distribution, version)
    try:
        version = importlib.metadata.version("minimax-voice-agent")
    except importlib.metadata.PackageNotFoundError:
        report(False, "Local project", "Run pip install -r requirements.txt from the project root")
    else:
        report(True, "Local project", version)

    if importlib.util.find_spec("pydantic_settings") is not None:
        try:
            from voice_agent.config import Settings
            from voice_agent.audio.config import AudioConfig
            from voice_agent.scene import load_scene

            settings = Settings()
            load_scene(settings.scene)
            report(True, "Configuration", f"scene={settings.scene}; llm={settings.llm_provider}")
            if settings.llm_provider == "minimax" or settings.tts_provider == "minimax":
                report(bool(settings.minimax_api_key.get_secret_value().strip()), "MiniMax key",
                       "Presence check only; value hidden, no API call made")
            if settings.llm_provider == "minimax":
                url = urlsplit(settings.minimax_base_url)
                report(url.scheme == "https" and bool(url.hostname) and not url.username,
                       "MiniMax endpoint", "HTTPS URL validation only")
            if settings.tts_provider == "minimax":
                tts_url = settings.tts_base_url.strip() or settings.minimax_base_url
                url = urlsplit(tts_url)
                report(url.scheme == "https" and bool(url.hostname) and not url.username,
                       "MiniMax TTS endpoint", "HTTPS URL validation only")
            if args.voice:
                report(settings.asr_provider in {"whisper", "xfyun"}, "ASR provider",
                       "Set ASR_PROVIDER=whisper or xfyun for voice-agent-listen")
                if settings.asr_provider == "xfyun":
                    credentials_present = all((
                        settings.xfyun_app_id.get_secret_value().strip(),
                        settings.xfyun_api_key.get_secret_value().strip(),
                        settings.xfyun_api_secret.get_secret_value().strip(),
                    ))
                    report(credentials_present, "XFYun credentials",
                           "APPID/APIKey/APISecret presence only; values hidden")
                    xfyun_url = urlsplit(settings.xfyun_asr_url)
                    report(xfyun_url.scheme == "wss" and bool(xfyun_url.hostname)
                           and not xfyun_url.username and not xfyun_url.query,
                           "XFYun endpoint", "WSS URL validation only")
                config = AudioConfig(
                    frame_ms=settings.audio_frame_ms, pre_roll_ms=settings.audio_pre_roll_ms,
                    start_speech_ms=settings.audio_start_speech_ms,
                    min_speech_ms=settings.audio_min_speech_ms,
                    end_silence_ms=settings.audio_end_silence_ms,
                    max_utterance_ms=settings.audio_max_utterance_ms,
                    wake_timeout_seconds=settings.wake_timeout_seconds,
                )
                report(config.max_utterance_ms / 1000 <= settings.max_audio_seconds,
                       "Audio limits", "Recorder limit must not exceed ASR limit")
                if settings.wake_provider == "porcupine":
                    importlib.import_module("pvporcupine")
                    report(bool(settings.porcupine_access_key.get_secret_value()),
                           "Porcupine key", "Presence check only")
                    report(Path(settings.porcupine_keyword_path).is_file(),
                           "Porcupine keyword", "Check .ppn path")
                    report(Path(settings.porcupine_model_path).is_file(),
                           "Porcupine language model", "Check .pv path")
        except Exception as exc:
            report(False, "Configuration", f"Check .env values ({type(exc).__name__})")
    print("No microphone recording, model downloads, or remote API calls were performed.")
    if errors:
        print(f"{len(errors)} check(s) failed. Resolve these before integration testing.")
        return 1
    print("Environment checks passed. Continue with docs/TESTING.md for real functional tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
