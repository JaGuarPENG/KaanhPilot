"""项目实机确认的 D435 RGB-D 流组合。

彩色使用 RGB8、深度使用 Z16；启动时精确解析并核对 Profile，
不支持时明确报错，不回退到 SDK 默认流。
"""

from typing import Final

from camera.contracts.cam_structs import CameraProfile


D435_640X480_30: Final = CameraProfile(640, 480, 30, "RGB8", 640, 480, 30, "Z16")
D435_1280X720_6: Final = CameraProfile(1280, 720, 6, "RGB8", 1280, 720, 6, "Z16")

D435_SUPPORTED_PROFILES: Final = (
    D435_640X480_30,
    D435_1280X720_6,
)


if __name__ == "__main__":
    for profile in D435_SUPPORTED_PROFILES:
        print(profile)
