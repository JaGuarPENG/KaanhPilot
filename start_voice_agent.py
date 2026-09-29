"""Start continuous voice listening using the current Python environment."""

import os
from pathlib import Path
import runpy
import sys


def main():
    voice_dir = Path(__file__).resolve().parent / "voice-agent"
    # Resolve .env, logs and configured relative paths from the voice project.
    os.chdir(voice_dir)
    sys.path.insert(0, str(voice_dir))
    runpy.run_module("voice_agent.runtime.cli", run_name="__main__")


if __name__ == "__main__":
    main()
