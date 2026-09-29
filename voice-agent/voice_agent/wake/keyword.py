import unicodedata


def canonical(text: str) -> str:
    return "".join(c.lower() for c in text if c.isalnum())


class KeywordWakeDetector:
    """Local ASR keyword gate. Only a phrase at the START can wake the agent."""

    def __init__(self, phrase: str = "你好小助手"):
        self.phrase = canonical(phrase)
        if not self.phrase:
            raise ValueError("Wake phrase must contain letters or numbers")

    def extract_command(self, text: str) -> str | None:
        chars = [(i, c.lower()) for i, c in enumerate(text) if c.isalnum()]
        normalized = "".join(c for _, c in chars)
        if not normalized.startswith(self.phrase):
            return None
        end = chars[len(self.phrase) - 1][0] + 1
        remainder = text[end:]
        while remainder and (remainder[0].isspace() or
                             unicodedata.category(remainder[0]).startswith("P")):
            remainder = remainder[1:]
        return remainder.strip()
