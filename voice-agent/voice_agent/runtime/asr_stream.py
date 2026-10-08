"""Bounded bridge between microphone frames and an asynchronous ASR consumer."""
import asyncio

from ..asr.base import ASRError, StreamingASRProvider


class StreamingRecognition:
    def __init__(self, provider: StreamingASRProvider, max_bytes=96000):
        self.queue = asyncio.Queue()
        self.buffered = 0
        self.max_bytes = max_bytes  # Three seconds, including pre-roll and connection setup.
        self.task = asyncio.create_task(provider.transcribe_stream(self._frames()))

    async def _frames(self):
        while True:
            pcm = await self.queue.get()
            if pcm is None:
                return
            self.buffered -= len(pcm)
            yield pcm

    def feed(self, pcm):
        if self.task.done():
            self.task.result()  # Propagate provider errors, never use an early final result.
            raise ASRError("语音识别在录音结束前终止。")
        if self.buffered + len(pcm) > self.max_bytes:
            raise ASRError("语音发送积压，已丢弃本次录音。")
        self.buffered += len(pcm)
        self.queue.put_nowait(pcm)

    async def finish(self):
        if self.task.done():
            self.task.result()
            raise ASRError("语音识别在录音结束前终止。")
        self.queue.put_nowait(None)
        return await self.task

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
