"""Gemini 305 的厂商特定 Profile 声明。"""

from typing import Final

from camera.contracts.models import CameraProfile

# 这两套 Profile 是当前经用户确认的可选配置；适配器不会静默替换为默认流。
G305_1280X800_30: Final = CameraProfile(1280, 800, 30, "MJPG", 1280, 800, 30, "Y16")
G305_848X480_60: Final = CameraProfile(848, 480, 60, "MJPG", 848, 480, 60, "Y16")
G305_SUPPORTED_PROFILES: Final = (G305_1280X800_30, G305_848X480_60)


if __name__ == "__main__":
    for profile in G305_SUPPORTED_PROFILES:
        print(profile)
