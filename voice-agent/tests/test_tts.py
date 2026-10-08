import unittest
from types import SimpleNamespace

from voice_agent.tts import MiniMaxTTS, SpeechOutput, SynthesizedAudio, TTSError


class Secret:
    def get_secret_value(self):
        return "test-placeholder"


def settings(**changes):
    values = dict(
        minimax_api_key=Secret(), minimax_base_url="https://api.minimax.io/v1",
        tts_base_url="", tts_timeout_seconds=60, tts_model="speech-2.8-turbo",
        tts_voice_id="Chinese (Mandarin)_Gentleman", tts_language_boost="Chinese", tts_speed=1.0,
        tts_volume=1.0, tts_pitch=0, tts_sample_rate=32000, tts_bitrate=128000,
        tts_max_audio_bytes=1024,
    )
    values.update(changes)
    return SimpleNamespace(**values)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeClient:
    def __init__(self, body):
        self.body, self.calls, self.closed = body, [], False

    async def post(self, path, json):
        self.calls.append((path, json))
        return FakeResponse(self.body)

    async def aclose(self):
        self.closed = True


class MiniMaxTTSTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthesis_decodes_hex_wav_and_sends_expected_settings(self):
        expected = b"RIFF-test-wave"
        client = FakeClient({"data": {"audio": expected.hex(), "status": 2},
                             "base_resp": {"status_code": 0}})
        tts = MiniMaxTTS(settings(), client=client)
        audio = await tts.synthesize(" 你好。 ")
        self.assertEqual(audio, SynthesizedAudio(expected, "wav"))
        path, payload = client.calls[0]
        self.assertEqual(path, "t2a_v2")
        self.assertEqual(payload["text"], "你好。")
        self.assertEqual(payload["model"], "speech-2.8-turbo")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["output_format"], "hex")
        self.assertEqual(payload["voice_setting"]["voice_id"], "Chinese (Mandarin)_Gentleman")
        self.assertEqual(payload["audio_setting"]["format"], "wav")
        await tts.close()
        self.assertTrue(client.closed)

    async def test_empty_text_is_rejected_before_request(self):
        client = FakeClient({})
        with self.assertRaises(TTSError):
            await MiniMaxTTS(settings(), client=client).synthesize("  ")
        self.assertEqual(client.calls, [])

    async def test_provider_error_is_rejected(self):
        body = {"data": None, "base_resp": {"status_code": 1001}}
        with self.assertRaisesRegex(TTSError, "拒绝"):
            await MiniMaxTTS(settings(), client=FakeClient(body)).synthesize("你好")

    async def test_invalid_hex_is_rejected(self):
        body = {"data": {"audio": "not-hex"}, "base_resp": {"status_code": 0}}
        with self.assertRaisesRegex(TTSError, "编码无效"):
            await MiniMaxTTS(settings(), client=FakeClient(body)).synthesize("你好")

    async def test_audio_size_limit_is_checked_before_decoding(self):
        body = {"data": {"audio": "00" * 11}, "base_resp": {"status_code": 0}}
        with self.assertRaisesRegex(TTSError, "大小限制"):
            await MiniMaxTTS(settings(tts_max_audio_bytes=10),
                             client=FakeClient(body)).synthesize("你好")

    async def test_speech_output_synthesizes_then_plays(self):
        calls, audio = [], SynthesizedAudio(b"wav", "wav")

        class Provider:
            async def synthesize(self, text):
                calls.append(("synthesize", text))
                return audio

        class Player:
            async def play(self, value):
                if value != audio:
                    raise AssertionError("player received different audio")
                calls.append(("play", value.format))

        await SpeechOutput(Provider(), Player()).speak("回复")
        self.assertEqual(calls, [("synthesize", "回复"), ("play", "wav")])


if __name__ == "__main__":
    unittest.main()
