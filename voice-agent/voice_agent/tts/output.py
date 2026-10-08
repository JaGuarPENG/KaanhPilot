from contextlib import aclosing

from .base import AudioPlayer, StreamingTTSProvider, TTSProvider


class SpeechOutput:
    def __init__(self, provider: TTSProvider, player: AudioPlayer):
        self.provider, self.player = provider, player

    async def speak(self, text: str):
        audio = await self.provider.synthesize(text)
        await self.player.play(audio)


class StreamingSpeechOutput:
    def __init__(self, provider: StreamingTTSProvider, player):
        self.provider, self.player = provider, player

    async def speak(self, text: str):
        # Close the HTTP stream even if the speaker fails or listening is cancelled.
        async with aclosing(self.provider.synthesize_stream(text)) as chunks:
            await self.player.play_stream(chunks)
