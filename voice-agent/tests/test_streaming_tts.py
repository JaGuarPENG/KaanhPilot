import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from voice_agent.tts import MiniMaxTTS, TTSError
from voice_agent.tts.base import PlaybackError
from voice_agent.tts.output import StreamingSpeechOutput
from voice_agent.tts.playback import PCMPlayer
from .test_tts import settings


def event(audio='', status=1, **extra):
    body = {'base_resp': {'status_code': 0}, 'data': {'audio': audio, 'status': status}, **extra}
    return ('data: ' + json.dumps(body) + '\n\n').encode()


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None):
        self.chunks, self.gate, self.closed = chunks, gate, False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index and self.gate is not None:
                await self.gate.wait()
            # SSE parsing must tolerate arbitrary network fragmentation.
            for start in range(0, len(chunk), 7):
                yield chunk[start:start + 7]

    async def aclose(self):
        self.closed = True


async def test_stream_plays_first_pcm_before_final_response(monkeypatch):
    played = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []

    class Device:
        def __init__(self, **kwargs):
            assert kwargs['samplerate'] == 32000
            assert kwargs['channels'] == 1
            assert kwargs['dtype'] == 'int16'

        def start(self):
            calls.append('start')

        def write(self, data):
            calls.append(bytes(data))
            loop.call_soon_threadsafe(played.set)

        def stop(self):
            calls.append('stop')

        def abort(self):
            calls.append('abort')

        def close(self):
            calls.append('close')

    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawOutputStream=Device))
    body = Body([event('01000200'), event('0300'), event(status=2)], played)

    def handle(request):
        payload = json.loads(request.content)
        assert payload['stream'] is True
        assert payload['stream_options']['exclude_aggregated_audio'] is True
        assert payload['audio_setting']['format'] == 'pcm'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=body)

    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(handle))
    try:
        await asyncio.wait_for(StreamingSpeechOutput(tts, PCMPlayer()).speak('已受理'), 2)
    finally:
        await tts.close()
    assert b''.join(c for c in calls if isinstance(c, bytes)) == b'\x01\x00\x02\x00\x03\x00'
    assert calls[0] == 'start'
    assert calls[-2:] == ['stop', 'close']
    assert 'abort' not in calls
    assert body.closed


@pytest.mark.parametrize('chunks', [
    [event('0000')],  # No final event.
    [event('not-hex'), event(status=2)],
    [event('00'), event(status=2)],  # Incomplete PCM sample.
    [event(status=2)],  # No audio.
    [event('0000', extra_info={'audio_format': 'mp3'})],
    [b'data: {"base_resp":{"status_code":1001,"status_msg":"private"}}\n\n'],
])
async def test_invalid_stream_fails_and_closes_without_leaking_provider_body(chunks):
    body = Body(chunks)
    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    try:
        with pytest.raises(TTSError) as error:
            async for _ in tts.synthesize_stream('你好'):
                pass
        assert 'private' not in str(error.value)
        assert body.closed
    finally:
        await tts.close()


async def test_stream_reassembles_sample_split_between_events():
    body = Body([event('01'), event('000200'), event(status=2)])
    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    try:
        chunks = [chunk async for chunk in tts.synthesize_stream('你好')]
        assert b''.join(chunk.data for chunk in chunks) == b'\x01\x00\x02\x00'
        assert all(chunk.sample_rate == 32000 for chunk in chunks)
    finally:
        await tts.close()


class Device:
    def __init__(self, **kwargs):
        self.writes = []
        self.closed = False
        self.aborted = False

    def start(self):
        pass

    def write(self, data):
        self.writes.append(bytes(data))

    def stop(self):
        pass

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


async def test_device_failure_closes_http_without_waiting_for_next_network_chunk(monkeypatch):
    class Broken(Device):
        def write(self, data):
            raise RuntimeError('device disconnected')

    device = Broken()
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawOutputStream=lambda **_: device))
    body = Body([event('0000'), event(status=2)], asyncio.Event())
    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    try:
        with pytest.raises(PlaybackError):
            await asyncio.wait_for(StreamingSpeechOutput(tts, PCMPlayer()).speak('你好'), 1)
        assert body.closed
        assert device.closed and device.aborted
    finally:
        await tts.close()


async def test_cancelling_playback_closes_device_and_http(monkeypatch):
    played = asyncio.Event()
    loop = asyncio.get_running_loop()

    class Started(Device):
        def write(self, data):
            super().write(data)
            loop.call_soon_threadsafe(played.set)

    device = Started()
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawOutputStream=lambda **_: device))
    body = Body([event('0000'), event(status=2)], asyncio.Event())
    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    task = asyncio.create_task(StreamingSpeechOutput(tts, PCMPlayer()).speak('你好'))
    try:
        await asyncio.wait_for(played.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
        assert device.closed and device.aborted
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await tts.close()


async def test_speak_waits_for_device_drain(monkeypatch):
    draining = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class Draining(Device):
        def stop(self):
            loop.call_soon_threadsafe(draining.set)
            assert release.wait(2)

    device = Draining()
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(RawOutputStream=lambda **_: device))
    body = Body([event('0000'), event(status=2)])
    tts = MiniMaxTTS(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    task = asyncio.create_task(StreamingSpeechOutput(tts, PCMPlayer()).speak('你好'))
    try:
        await asyncio.wait_for(draining.wait(), 1)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 1)
        assert device.closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await tts.close()


async def test_stream_cumulative_size_limit():
    body = Body([event('0000'), event('0000'), event(status=2)])
    tts = MiniMaxTTS(settings(tts_max_audio_bytes=2), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=body)))
    try:
        with pytest.raises(TTSError, match='大小限制'):
            async for _ in tts.synthesize_stream('你好'):
                pass
        assert body.closed
    finally:
        await tts.close()
