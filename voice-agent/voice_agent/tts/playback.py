import asyncio
import io
import queue
import sys
import threading
from contextlib import suppress

from .base import PlaybackError, SynthesizedAudio


class WavPlayer:
    """Play synthesized WAV from memory through the selected output device."""

    def __init__(self, device=None):
        self.device = device

    def _play(self, audio: SynthesizedAudio):
        if audio.format != "wav":
            raise PlaybackError("本地播放器只接受 WAV 音频。")
        try:
            import sounddevice as sd
            import soundfile as sf

            samples, sample_rate = sf.read(io.BytesIO(audio.data), dtype="float32", always_2d=True)
            if not len(samples):
                raise PlaybackError("合成语音为空。")
            sd.play(samples, sample_rate, device=self.device, blocking=True)
        except PlaybackError:
            raise
        except Exception as exc:
            raise PlaybackError("合成语音播放失败，请检查输出设备。") from exc

    async def play(self, audio: SynthesizedAudio):
        await asyncio.to_thread(self._play, audio)


class PCMPlayer:
    """One output stream per reply, with at most two seconds of queued PCM."""

    def __init__(self, device=None):
        self.device = device

    def _play_stream(self, pending, stop, sample_rate):
        output = None
        drained = False
        try:
            if sys.byteorder != "little":
                raise PlaybackError("PCM 播放器需要小端字节序。")
            import sounddevice as sd

            output = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16",
                                        device=self.device)
            output.start()
            while not stop.is_set():
                try:
                    data = pending.get(timeout=0.05)
                except queue.Empty:
                    continue
                if data is None:
                    output.stop()  # Drain the device before unpausing the microphone.
                    drained = True
                    return
                output.write(data)
        except Exception as exc:
            raise PlaybackError("流式语音播放失败，请检查输出设备。") from exc
        finally:
            if output is not None:
                try:
                    if not drained:
                        output.abort()
                finally:
                    output.close()

    async def play_stream(self, chunks):
        pending = queue.Queue(maxsize=100)
        stop = threading.Event()
        worker = None
        sample_rate = None
        iterator = aiter(chunks)

        async def next_chunk():
            receive = asyncio.create_task(anext(iterator))
            try:
                if worker is not None:
                    done, _ = await asyncio.wait((receive, worker),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if worker in done:
                        await worker
                        raise PlaybackError("播放器提前结束。")
                return await receive
            finally:
                if not receive.done():
                    receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)

        async def enqueue(data):
            while True:
                if worker.done():
                    await worker
                    raise PlaybackError("播放器提前结束。")
                try:
                    pending.put_nowait(data)
                    return
                except queue.Full:
                    await asyncio.sleep(0.01)

        try:
            while True:
                try:
                    chunk = await next_chunk()
                except StopAsyncIteration:
                    break
                if (not chunk.data or len(chunk.data) % 2 or chunk.sample_rate <= 0
                        or (sample_rate is not None and sample_rate != chunk.sample_rate)):
                    raise PlaybackError("PCM 音频参数无效或发生变化。")
                if worker is None:
                    sample_rate = chunk.sample_rate
                    worker = asyncio.create_task(asyncio.to_thread(
                        self._play_stream, pending, stop, sample_rate))
                # Small writes bound cancellation latency and playback queue memory.
                block_bytes = max(2, sample_rate // 50 * 2)
                for start in range(0, len(chunk.data), block_bytes):
                    await enqueue(chunk.data[start:start + block_bytes])
            if worker is None:
                raise PlaybackError("没有可播放的语音。")
            await enqueue(None)
            await asyncio.shield(worker)
        finally:
            stop.set()
            if worker is not None:
                with suppress(Exception):
                    await asyncio.shield(worker)
