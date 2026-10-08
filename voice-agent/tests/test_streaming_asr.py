import asyncio
import base64
import json

import pytest

from voice_agent.asr import ASRError, XFYunASR
from voice_agent.runtime.listener import VoiceListener
from voice_agent.runtime.conversation import LocalConversation
from voice_agent.runtime.asr_stream import StreamingRecognition
from voice_agent.audio.source import AudioGapError

from .test_core import make_agent
from .test_voice import config, frame, FakeVAD, say
from .test_xfyun import settings


class LiveSocket:
    def __init__(self):
        self.sent = []
        self.first = asyncio.Event()
        self.end = asyncio.Event()
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        self.first.set()
        if message['data']['status'] == 2:
            self.end.set()

    async def __aiter__(self):
        await self.first.wait()
        yield json.dumps({'code': 0, 'data': {'status': 1, 'result': {
            'sn': 1, 'ws': [{'cw': [{'w': '错误中间结果'}]}]}}})
        await self.end.wait()
        yield json.dumps({'code': 0, 'data': {'status': 2, 'result': {
            'sn': 2, 'pgs': 'rpl', 'rg': [1, 1],
            'ws': [{'cw': [{'w': '你好小助手，打开客厅的灯'}]}]}}})


async def no_sleep(_):
    await asyncio.sleep(0)


async def test_provider_sends_before_input_finishes_and_reframes_without_loss():
    socket = LiveSocket()
    proceed = asyncio.Event()

    async def frames():
        yield frame() * 5
        await proceed.wait()
        yield frame(False)

    provider = XFYunASR(settings(), connect=lambda _: socket, sleep=no_sleep)
    task = asyncio.create_task(provider.transcribe_stream(frames()))
    try:
        await asyncio.wait_for(socket.first.wait(), 1)
        assert not task.done()
        assert not socket.end.is_set()
        proceed.set()
        assert await asyncio.wait_for(task, 1) == '你好小助手，打开客厅的灯'
        audio = [base64.b64decode(m['data']['audio']) for m in socket.sent
                 if 'audio' in m['data']]
        assert b''.join(audio) == frame() * 5 + frame(False)
        assert all(len(chunk) <= 1280 for chunk in audio)
        assert [m['data']['status'] for m in socket.sent] == [0, 1, 2]
        assert socket.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class StreamingASR:
    def __init__(self, text='你好小助手，打开客厅的灯'):
        self.text = text
        self.chunks = []
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def transcribe_stream(self, frames):
        self.started.set()
        try:
            async for pcm in frames:
                self.chunks.append(pcm)
            return self.text
        finally:
            self.closed.set()

    async def transcribe(self, audio):
        raise AssertionError('whole-file ASR should not be used')


async def test_listener_streams_before_silence_preserves_preroll_and_submits_once():
    asr = StreamingASR()
    events = []
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent(scene='home_assistant')),
                             emit=events.append)
    for speech in [False] * 4 + [True] * 5:
        await listener.process_frame(frame(speech))
        await asyncio.sleep(0)
    await asyncio.wait_for(asr.started.wait(), 1)
    assert asr.chunks
    assert not any(e['event'] == 'agent_response' for e in events)
    for _ in range(4):
        await listener.process_frame(frame(False))
    assert b''.join(asr.chunks) == frame(False) + frame() * 5 + frame(False) * 4
    assert len([e for e in events if e['event'] == 'agent_response']) == 1
    assert asr.closed.is_set()
    assert listener.state == 'idle'


@pytest.mark.parametrize('speech', [[True] * 30, [True] * 2 + [False] * 4])
async def test_rejected_recording_cancels_stream_without_submitting(speech):
    asr = StreamingASR()
    events = []
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent()), emit=events.append)
    for voiced in speech:
        await listener.process_frame(frame(voiced))
        await asyncio.sleep(0)
    assert asr.closed.is_set()
    assert not any(e['event'] == 'agent_response' for e in events)


async def test_stream_background_text_never_reaches_agent():
    events = []
    asr = StreamingASR('背景讲话')
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent()), emit=events.append)
    await say(listener)
    assert asr.chunks
    assert not any(e['event'] in ('command', 'agent_response') for e in events)


async def test_stream_failure_does_not_fallback_to_duplicate_recognition():
    class Broken(StreamingASR):
        async def transcribe_stream(self, frames):
            self.started.set()
            raise ASRError('offline')

    events = []
    asr = Broken()
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent()), emit=events.append)
    await say(listener)
    assert asr.started.is_set()
    assert not any(e['event'] == 'agent_response' for e in events)
    assert any(e['event'] == 'error' for e in events)


async def test_listener_with_real_xfyun_protocol_submits_only_final_correction():
    socket = LiveSocket()
    provider = XFYunASR(settings(), connect=lambda _: socket, sleep=no_sleep)
    events = []
    listener = VoiceListener(config(), FakeVAD(), provider,
                             LocalConversation(make_agent(scene='home_assistant')),
                             emit=events.append)
    for _ in range(5):
        await listener.process_frame(frame())
        await asyncio.sleep(0)
    await asyncio.wait_for(socket.first.wait(), 1)
    assert not any(e['event'] == 'command' for e in events)
    for _ in range(4):
        await listener.process_frame(frame(False))
    responses = [e['response'] for e in events if e['event'] == 'agent_response']
    assert len(responses) == 1
    assert responses[0]['transcript'] == '打开客厅的灯'
    assert responses[0]['actions'][0]['type'] == 'set_light'
    assert socket.closed


@pytest.mark.parametrize('kind', ['early_final', 'timeout'])
async def test_provider_early_final_or_timeout_closes_connection(kind):
    class InterruptedSocket(LiveSocket):
        async def __aiter__(self):
            await self.first.wait()
            if kind == 'timeout':
                await asyncio.Event().wait()
            yield json.dumps({'code': 0, 'data': {'status': 2}})

    socket = InterruptedSocket()

    async def frames():
        yield frame() * 4
        await asyncio.Event().wait()

    provider = XFYunASR(settings(xfyun_asr_timeout_seconds=0.05),
                         connect=lambda _: socket, sleep=no_sleep)
    with pytest.raises(ASRError):
        await asyncio.wait_for(provider.transcribe_stream(frames()), 1)
    assert socket.closed


async def test_provider_cancellation_closes_connection():
    socket = LiveSocket()

    async def frames():
        yield frame() * 4
        await asyncio.Event().wait()

    provider = XFYunASR(settings(), connect=lambda _: socket, sleep=no_sleep)
    task = asyncio.create_task(provider.transcribe_stream(frames()))
    await asyncio.wait_for(socket.first.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closed


async def test_bounded_bridge_rejects_network_backlog():
    class Slow(StreamingASR):
        async def transcribe_stream(self, frames):
            await asyncio.Event().wait()

    bridge = StreamingRecognition(Slow(), max_bytes=len(frame()))
    try:
        bridge.feed(frame())
        with pytest.raises(ASRError, match='积压'):
            bridge.feed(frame())
    finally:
        await bridge.close()
    assert bridge.task.done()


async def test_audio_gap_and_listener_exit_close_pending_stream():
    asr = StreamingASR()
    events = []
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent()), emit=events.append)

    class Source:
        def __init__(self):
            self.count = 0

        async def read(self):
            self.count += 1
            await asyncio.sleep(0)
            if self.count <= 5:
                return frame()
            if self.count == 6:
                raise AudioGapError()
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await listener.run(Source())
    assert asr.closed.is_set()
    assert any(e['event'] == 'audio_gap' for e in events)
    assert not any(e['event'] == 'agent_response' for e in events)


async def test_streaming_can_be_disabled_for_whole_file_fallback():
    class Both(StreamingASR):
        async def transcribe(self, audio):
            assert audio.startswith(b'RIFF')
            return self.text

    asr = Both()
    events = []
    listener = VoiceListener(config(), FakeVAD(), asr,
                             LocalConversation(make_agent(scene='home_assistant')),
                             streaming_asr=False, emit=events.append)
    await say(listener)
    assert not asr.started.is_set()
    assert any(e['event'] == 'agent_response' for e in events)
