import argparse
import asyncio
import json
import sys

from ..audio.config import AudioConfig
from ..audio.source import MicrophoneSource
from ..audio.vad import WebRTCVAD
from ..bootstrap import build_services, build_tts
from ..asr.base import ASRError
from ..config import Settings
from ..wake.keyword import KeywordWakeDetector
from .listener import VoiceListener
from .conversation import LocalConversation
from ..tts import PCMPlayer, SpeechOutput, StreamingSpeechOutput, WavPlayer


def emit(event):
    # Machine-consumable response JSON goes to stdout; lifecycle diagnostics to stderr.
    if event["event"] == "agent_response":
        print(json.dumps(event["response"], ensure_ascii=False), flush=True)
    else:
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)


async def listen(settings, device, output_device=None):
    if settings.asr_provider not in {"whisper", "xfyun"}:
        raise ValueError("Set ASR_PROVIDER=whisper or xfyun for continuous listening")
    config = AudioConfig(
        frame_ms=settings.audio_frame_ms, pre_roll_ms=settings.audio_pre_roll_ms,
        start_speech_ms=settings.audio_start_speech_ms,
        min_speech_ms=settings.audio_min_speech_ms,
        end_silence_ms=settings.audio_end_silence_ms,
        max_utterance_ms=settings.audio_max_utterance_ms,
        wake_timeout_seconds=settings.wake_timeout_seconds,
    )
    if config.max_utterance_ms / 1000 > settings.max_audio_seconds:
        raise ValueError("AUDIO_MAX_UTTERANCE_MS exceeds MAX_AUDIO_SECONDS")
    agent, provider, asr = build_services(settings)
    speech_provider = None
    frame_wake = None
    try:
        speech_provider = build_tts(settings)
        speaker = None
        if settings.tts_provider == "minimax":
            speaker = (StreamingSpeechOutput(speech_provider, PCMPlayer(output_device))
                       if settings.tts_streaming else
                       SpeechOutput(speech_provider, WavPlayer(output_device)))
        if settings.wake_provider == "porcupine":
            from ..wake.porcupine import PorcupineWakeDetector

            frame_wake = PorcupineWakeDetector(
                settings.porcupine_access_key.get_secret_value(), settings.porcupine_keyword_path,
                settings.porcupine_model_path, sensitivity=settings.wake_sensitivity,
            )
        elif settings.wake_provider == "xfyun":
            from ..wake.xfyun import XFYunWakeDetector

            print("正在初始化讯飞本地唤醒 SDK……", file=sys.stderr, flush=True)
            frame_wake = XFYunWakeDetector(settings)
        vad = WebRTCVAD(config.sample_rate, settings.vad_aggressiveness)
        message = ("正在加载本地语音模型……" if settings.asr_provider == "whisper"
                   else "正在检查科大讯飞语音听写配置……")
        print(message, file=sys.stderr, flush=True)
        await asr.prepare()
        with MicrophoneSource(config, device=device) as source:
            listener = VoiceListener(
                config, vad, asr, LocalConversation(agent),
                keyword=KeywordWakeDetector(settings.wake_phrase),
                frame_wake=frame_wake, emit=emit, set_paused=source.set_paused,
                audit=agent.audit,
                speaker=speaker,
                streaming_asr=settings.xfyun_asr_streaming,
            )
            await listener.run(source)
    finally:
        if frame_wake is not None:
            frame_wake.close()
        try:
            if speech_provider is not None:
                await speech_provider.close()
        finally:
            try:
                await provider.close()
                agent.audit.record("service.stopped")
            finally:
                agent.audit.close()


def main():
    parser = argparse.ArgumentParser(description="自动唤醒 + 静音断句 + JSON Agent")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", help="输入设备编号；省略则使用系统默认麦克风")
    parser.add_argument("--output-device", help="输出设备编号；省略则使用系统默认扬声器")
    args = parser.parse_args()
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    device = int(args.device) if args.device and args.device.isdigit() else args.device
    output_device = (int(args.output_device) if args.output_device and args.output_device.isdigit()
                     else args.output_device)
    try:
        asyncio.run(listen(Settings(), device, output_device))
    except KeyboardInterrupt:
        print("监听已停止。", file=sys.stderr)
    except (ImportError, ValueError, RuntimeError, ASRError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
