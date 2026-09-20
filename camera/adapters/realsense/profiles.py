"""RealSense D435 的明确 RGB-D Profile（USB 3.x）。

来源： https://www.realsenseai.com/cn/products/d435/
及该页链接的 D400 Series Datasheet（August 2025），表 4-2，页 75-76。
深度 1280x720 最高 30 fps；90 fps 仅适用于较低分辨率的深度流，
不能推导出 1280x720@90 或 RGB-D@90。表 4-2 另列出 RGB 848x480@60。

下列是项目选用的组合，不是设备所有模式的枚举。SDK 将 RGB 传感器的
原生格式转换为 RGB8，深度使用 Z16；启动时仍须精确解析并核对 Profile，
USB 带宽/固件不支持时明确报错，不回退到 SDK 默认流。
"""

from typing import Final

from camera.contracts.cam_structs import CameraProfile


D435_640X480_30: Final = CameraProfile(640, 480, 30, "RGB8", 640, 480, 30, "Z16")
D435_848X480_30: Final = CameraProfile(848, 480, 30, "RGB8", 848, 480, 30, "Z16")
D435_848X480_60: Final = CameraProfile(848, 480, 60, "RGB8", 848, 480, 60, "Z16")
D435_1280X720_30: Final = CameraProfile(1280, 720, 30, "RGB8", 1280, 720, 30, "Z16")
# 彩色最高 1920x1080，深度仍为 1280x720；D2C 输出尺寸以彩色流为准。
D435_1920X1080_30: Final = CameraProfile(1920, 1080, 30, "RGB8", 1280, 720, 30, "Z16")

D435_SUPPORTED_PROFILES: Final = (
    D435_640X480_30,
    D435_848X480_30,
    D435_848X480_60,
    D435_1280X720_30,
    D435_1920X1080_30,
)


if __name__ == "__main__":
    for profile in D435_SUPPORTED_PROFILES:
        print(profile)
