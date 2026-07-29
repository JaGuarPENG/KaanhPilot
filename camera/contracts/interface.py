"""相机模块的厂商无关公共接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from camera.contracts.models import AlignedRGBDObservation, CameraCapabilities, CameraProfile, CameraState


class Camera(ABC):
    """一个设备对应一个独占的后台相机流。"""

    @property
    @abstractmethod
    def state(self) -> CameraState:
        """返回当前生命周期状态。"""

    @property
    @abstractmethod
    def camera_id(self) -> str | None:
        """返回设备稳定序列号；尚未启动时可为 None。"""

    @abstractmethod
    def capabilities(self) -> CameraCapabilities:
        """返回相机能力和预期可选 Profile。"""

    @abstractmethod
    def start(self) -> None:
        """启动后台采集；成功返回前必须已经发布首个完整观测。"""

    @abstractmethod
    def stop(self) -> None:
        """停止后台采集并清理 SDK 流资源。"""

    @abstractmethod
    def close(self) -> None:
        """永久关闭相机实例；关闭后不允许再次启动。"""

    @abstractmethod
    def get_latest_observation(self) -> AlignedRGBDObservation | None:
        """获取最新不可变观测；尚未有帧时返回 None，失败时抛出异常。"""

    def __enter__(self) -> "Camera":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


if __name__ == "__main__":
    print("Camera 是抽象接口，请运行 camera.demo 测试真实相机。")
