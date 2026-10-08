import io
import struct
import unittest
import wave

from voice_agent.audio.config import AudioConfig
from voice_agent.audio.encoding import pcm_to_wav
from voice_agent.audio.segmenter import SpeechSegmenter
from voice_agent.audio.source import AudioGapError, MicrophoneSource
from voice_agent.runtime.conversation import LocalConversation
from voice_agent.runtime.listener import VoiceListener
from voice_agent.wake.keyword import KeywordWakeDetector
from voice_agent.wake.porcupine import PorcupineWakeDetector

from .test_core import CountingExecutor, ScriptedLLM, decision, make_agent, move


def config(**changes):
    values = dict(frame_ms=10, pre_roll_ms=30, start_speech_ms=20, min_speech_ms=30,
                  end_silence_ms=40, max_utterance_ms=300, wake_timeout_seconds=1)
    values.update(changes)
    return AudioConfig(**values)


def frame(speech=True):
    return (b"\x01\x00" if speech else b"\x00\x00") * 160


class FakeVAD:
    def is_speech(self, pcm):
        return pcm == frame(True)


class ScriptedASR:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.calls = []

    async def transcribe(self, audio):
        self.calls.append(audio)
        with wave.open(io.BytesIO(audio), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
        value = next(self.texts)
        if isinstance(value, Exception):
            raise value
        return value


async def say(listener):
    for speech in [True] * 5 + [False] * 4:
        await listener.process_frame(frame(speech))


class SegmentTests(unittest.TestCase):
    def test_silence_produces_no_recording(self):
        segmenter = SpeechSegmenter(config())
        for _ in range(100):
            self.assertIsNone(segmenter.feed(frame(False), False))
        self.assertFalse(segmenter.recording)

    def test_brief_pause_does_not_finish_sentence(self):
        segmenter = SpeechSegmenter(config())
        for speech in [True] * 5 + [False] * 3 + [True] * 5 + [False] * 3:
            self.assertIsNone(segmenter.feed(frame(speech), speech))
        result = segmenter.feed(frame(False), False)
        self.assertEqual(result.reason, "silence")
        self.assertEqual(result.speech_ms, 100)

    def test_short_noise_is_rejected(self):
        segmenter = SpeechSegmenter(config())
        result = None
        for speech in [True] * 2 + [False] * 4:
            result = segmenter.feed(frame(speech), speech)
        self.assertEqual(result.reason, "too_short")

    def test_pre_roll_is_preserved_without_duplicating_start(self):
        segmenter = SpeechSegmenter(config())
        incoming = [False] * 4 + [True] * 5 + [False] * 4
        result = None
        for speech in incoming:
            result = segmenter.feed(frame(speech), speech)
        self.assertEqual(result.pcm, frame(False) + frame(True) * 5 + frame(False) * 4)

    def test_max_recording_limit(self):
        segmenter = SpeechSegmenter(config())
        results = [segmenter.feed(frame(), True) for _ in range(30)]
        self.assertEqual(results[-1].reason, "limit")
        self.assertFalse(segmenter.recording)

    def test_invalid_frame_length(self):
        with self.assertRaises(ValueError):
            SpeechSegmenter(config()).feed(b"x", True)

    def test_invalid_config(self):
        for changes in [{"frame_ms": 25}, {"sample_rate": 44100}, {"pre_roll_ms": 10},
                        {"max_utterance_ms": 50}, {"end_silence_ms": 0}]:
            with self.assertRaises(ValueError):
                config(**changes)

    def test_wav_roundtrip(self):
        pcm = frame() * 3
        with wave.open(io.BytesIO(pcm_to_wav(pcm)), "rb") as wav:
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)


class WakeTests(unittest.TestCase):
    def test_chinese_wake_with_punctuation_and_command(self):
        wake = KeywordWakeDetector()
        self.assertEqual(wake.extract_command("你好，小助手，打开客厅的灯。"), "打开客厅的灯。")
        self.assertEqual(wake.extract_command("你好小助手！"), "")

    def test_phrase_mentioned_in_middle_does_not_wake(self):
        self.assertIsNone(KeywordWakeDetector().extract_command("不要说你好小助手"))

    def test_custom_wake(self):
        wake = KeywordWakeDetector("小明同学")
        self.assertEqual(wake.extract_command("小明同学，去厨房"), "去厨房")
        self.assertIsNone(wake.extract_command("你好小助手"))

    def test_porcupine_reframes_without_losing_samples(self):
        class Engine:
            frame_length = 512

            def __init__(self):
                self.received = []

            def process(self, values):
                self.received.extend(values)
                return -1

        wake = PorcupineWakeDetector.__new__(PorcupineWakeDetector)
        wake.engine, wake.buffer = Engine(), bytearray()
        samples = list(range(1440))
        for start in range(0, 1440, 480):
            self.assertFalse(wake.process(struct.pack("<480h", *samples[start:start + 480])))
        self.assertEqual(wake.engine.received, samples[:1024])
        self.assertEqual(len(wake.buffer), (1440 - 1024) * 2)


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    def setup_listener(self, texts, frame_wake=None, speaker=None):
        self.events, self.pauses = [], []
        self.now = 0.0
        self.asr = ScriptedASR(texts)
        self.agent = make_agent(scene="home_assistant")
        listener = VoiceListener(config(), FakeVAD(), self.asr, LocalConversation(self.agent),
                                 frame_wake=frame_wake, emit=self.events.append,
                                 set_paused=self.pauses.append, clock=lambda: self.now,
                                 speaker=speaker)
        return listener

    def responses(self):
        return [event["response"] for event in self.events if event["event"] == "agent_response"]

    async def test_background_speech_never_reaches_agent(self):
        listener = self.setup_listener(["打开客厅的灯"])
        await say(listener)
        self.assertEqual(listener.state, "idle")
        self.assertEqual(len(self.agent.sessions.sessions), 0)
        self.assertEqual(self.responses(), [])
        self.assertNotIn("打开客厅的灯", str(self.events))

    async def test_wake_plus_command_in_same_utterance(self):
        listener = self.setup_listener(["你好小助手，打开客厅的灯"])
        await say(listener)
        self.assertEqual(self.responses()[0]["actions"][0]["type"], "set_light")
        self.assertEqual(self.responses()[0]["transcript"], "打开客厅的灯")
        self.assertEqual(listener.state, "idle")
        self.assertEqual(self.pauses, [True, False])

    async def test_separate_wake_then_command(self):
        listener = self.setup_listener(["你好小助手", "打开客厅的灯"])
        await say(listener)
        self.assertEqual(listener.state, "armed")
        self.assertEqual(self.responses(), [])
        await say(listener)
        self.assertEqual(len(self.responses()), 1)
        self.assertEqual(listener.state, "idle")

    async def test_reply_is_spoken_while_microphone_remains_paused(self):
        class Speaker:
            async def speak(inner, text):
                self.assertTrue(self.pauses[-1])
                inner.text = text

        speaker = Speaker()
        listener = self.setup_listener(["你好小助手，打开客厅的灯"], speaker=speaker)
        await say(listener)
        self.assertEqual(speaker.text, self.responses()[0]["reply"])
        self.assertEqual(self.pauses, [True, False])
        self.assertIn("tts_start", [event["event"] for event in self.events])
        self.assertIn("tts_finished", [event["event"] for event in self.events])

    async def test_tts_failure_does_not_retry_completed_action(self):
        class Speaker:
            async def speak(self, text):
                raise RuntimeError("speaker unavailable")

        listener = self.setup_listener(["你好小助手，打开客厅的灯"], speaker=Speaker())
        await say(listener)
        names = [event["event"] for event in self.events]
        self.assertEqual(len(self.responses()), 1)
        self.assertEqual(len(self.responses()[0]["action_results"]), 1)
        self.assertEqual(self.responses()[0]["action_results"][0]["status"], "simulated")
        self.assertIn("tts_error", names)
        self.assertNotIn("error", names)
        self.assertEqual(listener.state, "idle")

    async def test_wake_timeout_returns_idle(self):
        listener = self.setup_listener(["你好小助手", "打开客厅的灯"])
        await say(listener)
        self.now = 1.01
        listener.tick()
        self.assertEqual(listener.state, "idle")
        await say(listener)
        self.assertEqual(self.responses(), [])

    async def test_timeout_does_not_interrupt_started_command(self):
        listener = self.setup_listener(["你好小助手", "打开客厅的灯"])
        await say(listener)
        self.now = 0.9
        await listener.process_frame(frame())
        await listener.process_frame(frame())
        self.now = 1.1
        for speech in [True] * 3 + [False] * 4:
            await listener.process_frame(frame(speech))
        self.assertEqual(len(self.responses()), 1)

    async def test_empty_asr_does_not_submit(self):
        listener = self.setup_listener([" "])
        await say(listener)
        self.assertEqual(self.responses(), [])
        self.assertEqual(listener.state, "idle")

    async def test_asr_error_resets_state_and_microphone(self):
        listener = self.setup_listener([RuntimeError("backend failure")])
        await say(listener)
        self.assertEqual(listener.state, "idle")
        self.assertEqual(self.pauses, [True, False])
        self.assertTrue(any(event["event"] == "error" for event in self.events))

    async def test_recording_limit_never_submits_truncated_command(self):
        listener = self.setup_listener([])
        for _ in range(30):
            await listener.process_frame(frame())
        self.assertEqual(self.asr.calls, [])
        self.assertEqual(self.responses(), [])

    async def test_dedicated_wake_avoids_idle_asr(self):
        class Wake:
            def __init__(self):
                self.detect = False

            def process(self, pcm):
                return self.detect

            def reset(self):
                self.detect = False

        wake = Wake()
        listener = self.setup_listener(["打开客厅的灯"], frame_wake=wake)
        await say(listener)
        self.assertEqual(self.asr.calls, [])
        wake.detect = True
        await listener.process_frame(frame())
        self.assertEqual(listener.state, "armed")
        await say(listener)
        self.assertEqual(len(self.responses()), 1)

    async def test_subsequent_command_requires_another_wake(self):
        listener = self.setup_listener(["你好小助手，打开客厅的灯", "关闭客厅的灯"])
        await say(listener)
        await say(listener)
        self.assertEqual(len(self.responses()), 1)

    async def test_voice_action_executes_immediately_and_followup_keeps_session(self):
        executor = CountingExecutor()
        agent = make_agent(ScriptedLLM([decision([move()]), decision()]), executor=executor)
        conversation = LocalConversation(agent)
        first = await conversation.handle("去厨房")
        self.assertEqual(first.status, "success")
        self.assertEqual(len(executor.calls), 1)
        result = await conversation.handle("现在怎么样？")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.session_id, first.session_id)
        self.assertEqual(len(executor.calls), 1)


class MicrophoneQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_overflow_discards_partial_audio(self):
        source = MicrophoneSource(config())
        for _ in range(source.queue.maxsize + 1):
            source._callback(frame(), 160, None, False)
        with self.assertRaises(AudioGapError):
            await source.read()
        self.assertTrue(source.queue.empty())

    async def test_paused_audio_cannot_replay_after_processing(self):
        source = MicrophoneSource(config())
        source._callback(frame(), 160, None, False)
        source.set_paused(True)
        source._callback(frame(), 160, None, False)
        source.set_paused(False)
        self.assertTrue(source.queue.empty())
        source._callback(frame(), 160, None, False)
        self.assertEqual(await source.read(), frame())


if __name__ == "__main__":
    unittest.main()
