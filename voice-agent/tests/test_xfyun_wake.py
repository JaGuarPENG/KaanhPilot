import ctypes as C
import threading
import unittest

from voice_agent.config import Settings
from voice_agent.wake._aikit import BaseData, OutputData
from voice_agent.wake.xfyun import XFYunWakeDetector, WakeWords, sdk_paths
from voice_agent.wake.base import WakeDetectorError
from . import test_voice as voice_tests

frame = voice_tests.frame


class FakeAPI:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []
        self.writes = []
        self.data = BaseData()

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            if name == self.fail:
                return 123
            if name == 'AIKIT_RegisterAbilityCallback':
                self.callbacks = args[1]
            elif name == 'AIKIT_Start':
                args[-1]._obj.value = 42
            elif name == 'AIKITBuilder_Create':
                return 99
            elif name == 'AIKITBuilder_BuildParam':
                return 88
            elif name == 'AIKITBuilder_BuildData':
                return C.pointer(self.data)
            elif name == 'AIKITBuilder_AddBuf':
                data = args[1]._obj
                self.writes.append((C.string_at(data.data, data.len), data.status))
            return 0
        return call

    def hit(self, empty=False, handle=42):
        buf = C.create_string_buffer(b'{"keyword":"test"}')
        node = BaseData()
        node.key, node.value, node.len = b'result', C.cast(buf, C.c_void_p), len(buf.value)
        output = OutputData(C.pointer(node), 0 if empty else 1, C.sizeof(node))
        self.callbacks.outputCB(handle, C.pointer(output))


def settings(**extra):
    return Settings(_env_file=None, xfyun_app_id='test', xfyun_api_key='test-key',
                    xfyun_api_secret='test-secret', **extra)


class XFYunWakeTests(unittest.TestCase):
    def test_keyword_stripping_uses_sdk_words_and_ignores_wake_only_suffix(self):
        words = WakeWords('小佳;\n小佳小佳;')
        self.assertEqual(words.extract_command('小佳小佳，给我可乐'), '给我可乐')
        self.assertEqual(words.extract_command('佳。'), '')
        self.assertIsNone(words.extract_command('给我可乐'))

    def create(self, api=None):
        api = api or FakeAPI()
        detector = XFYunWakeDetector(settings(), api=api)
        self.addCleanup(detector.close)
        return detector, api

    def test_resolves_bundled_win64_sdk_without_initialization(self):
        dll, models = sdk_paths(settings().xfyun_wake_sdk_dir)
        self.assertEqual(dll.name, '64')
        self.assertTrue((models / 'IVW_MLP_1').is_file())

    def test_requires_credentials_before_loading_sdk(self):
        with self.assertRaisesRegex(ValueError, 'XFYUN_APP_ID'):
            XFYunWakeDetector(Settings(_env_file=None), api=FakeAPI())

    def test_reframes_audio_and_preserves_sample_bytes(self):
        detector, api = self.create()
        detector.process(b'\x01\x00' * 160)
        self.assertEqual(api.writes, [])
        detector.process(b'\x02\x00' * 480)
        self.assertEqual([len(data) for data, _ in api.writes], [640, 640])
        self.assertEqual([status for _, status in api.writes], [0, 1])
        self.assertEqual(b''.join(data for data, _ in api.writes),
                         b'\x01\x00' * 160 + b'\x02\x00' * 480)

    def test_callback_from_native_thread_ends_session_and_next_frame_restarts(self):
        detector, api = self.create()
        worker = threading.Thread(target=api.hit)
        worker.start()
        worker.join()
        self.assertTrue(detector.process(b'\x00\x00' * 160))
        self.assertEqual(api.calls.count('AIKIT_End'), 1)
        api.hit()  # late output while session is stopped must not wake next turn
        self.assertFalse(detector.process(b'\x01\x00' * 320))
        self.assertEqual(api.calls.count('AIKIT_Start'), 2)
        self.assertEqual(api.writes[-1][1], 0)

    def test_empty_callback_does_not_wake(self):
        detector, api = self.create()
        api.hit(empty=True)
        self.assertFalse(detector.process(b'\x00\x00' * 320))

    def test_stale_session_callback_does_not_wake(self):
        detector, api = self.create()
        api.hit(handle=999)
        self.assertFalse(detector.process(b'\x00\x00' * 320))

    def test_stale_session_error_does_not_poison_current_session(self):
        detector, api = self.create()
        api.callbacks.errorCB(999, 123, b'old-session')
        self.assertFalse(detector.process(b'\x00\x00' * 320))
        api.callbacks.errorCB(42, 456, b'current-session')
        with self.assertRaisesRegex(RuntimeError, 'AIKit wake error 456'):
            detector.process(b'\x00\x00' * 320)

    def test_write_failure_is_reported_and_close_still_releases_sdk(self):
        detector, api = self.create()
        api.fail = 'AIKIT_Write'
        with self.assertRaisesRegex(RuntimeError, 'AIKIT_Write'):
            detector.process(b'\x00\x00' * 320)
        detector.close()
        self.assertIn('AIKIT_End', api.calls)
        self.assertIn('AIKIT_UnInit', api.calls)

    def test_async_error_does_not_expose_sdk_description(self):
        detector, api = self.create()
        api.callbacks.errorCB(None, 123, b'test-secret')
        with self.assertRaisesRegex(RuntimeError, '^AIKit wake error 123$'):
            detector.process(b'\x00\x00' * 320)

    def test_partial_startup_failure_cleans_resources_and_releases_singleton(self):
        api = FakeAPI('AIKIT_LoadData')
        with self.assertRaisesRegex(RuntimeError, 'AIKIT_LoadData'):
            XFYunWakeDetector(settings(), api=api)
        self.assertIn('AIKIT_EngineUnInit', api.calls)
        self.assertIn('AIKIT_UnInit', api.calls)
        self.assertNotIn('AIKIT_UnLoadData', api.calls)
        self.create()

    def test_close_is_idempotent_and_rejects_further_audio(self):
        detector, api = self.create()
        detector.close()
        detector.close()
        self.assertEqual(api.calls.count('AIKIT_UnInit'), 1)
        self.assertEqual(api.calls.count('AIKIT_UnLoadData'), 1)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            detector.process(b'\x00\x00')

    def test_only_one_sdk_instance(self):
        self.create()
        with self.assertRaisesRegex(RuntimeError, 'Only one'):
            XFYunWakeDetector(settings(), api=FakeAPI())


class XFYunListenerTests(unittest.IsolatedAsyncioTestCase):
    setup_listener = voice_tests.ListenerTests.setup_listener
    responses = voice_tests.ListenerTests.responses

    async def test_wake_only_does_not_reach_agent_even_with_different_default_phrase(self):
        for transcript in ('小佳', '佳'):
            with self.subTest(transcript=transcript):
                api = FakeAPI()
                detector = XFYunWakeDetector(settings(), api=api)
                try:
                    listener = self.setup_listener([transcript], frame_wake=detector)
                    for _ in range(5):
                        await listener.process_frame(frame())
                    api.hit()
                    await listener.process_frame(frame())
                    for _ in range(4):
                        await listener.process_frame(frame(False))
                    self.assertEqual(self.responses(), [])
                    self.assertEqual(listener.state, 'armed')
                finally:
                    detector.close()
    async def test_preroll_is_only_replayed_after_wake(self):
        class Wake:
            use_preroll = True
            detect = False

            def process(self, pcm):
                return self.detect

            def reset(self):
                self.detect = False

        wake = Wake()
        listener = self.setup_listener(['打开客厅的灯'], frame_wake=wake)
        for _ in range(5):
            await listener.process_frame(frame())
        self.assertEqual(self.asr.calls, [])
        wake.detect = True
        await listener.process_frame(frame())
        self.assertTrue(listener.segmenter.recording)
        for _ in range(4):
            await listener.process_frame(frame(False))
        self.assertEqual(len(self.responses()), 1)

    async def test_wake_failure_stops_instead_of_repeating_each_frame(self):
        class BrokenWake:
            def process(self, pcm):
                raise RuntimeError('SDK failure')

        listener = self.setup_listener([], frame_wake=BrokenWake())
        with self.assertRaisesRegex(WakeDetectorError, 'SDK failure'):
            await listener.process_frame(frame())
        self.assertEqual(self.asr.calls, [])
