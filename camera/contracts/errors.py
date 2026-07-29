"""相机模块的异常类型。

本文件不依赖任何厂商 SDK，使调用方能够统一处理不同相机的失败情形。
"""


class CameraError(RuntimeError):
    """相机模块的基类异常。"""


class CameraNotFoundError(CameraError):
    """请求的相机设备不存在或索引越界。"""


class CameraProfileError(CameraError):
    """设备不支持所请求的明确流配置。"""


class CameraStateError(CameraError):
    """在当前相机生命周期状态下调用了不允许的操作。"""


class CameraTimeoutError(CameraError):
    """在规定时间内没有获得完整的 RGB-D 帧。"""


class CameraStreamError(CameraError):
    """设备断开、SDK 报错或采集线程异常导致的终止性流失败。"""


if __name__ == "__main__":
    # 独立运行时验证异常层级，便于快速确认调用方可以捕获统一异常。
    try:
        raise CameraProfileError("示例：不支持的相机 Profile")
    except CameraError as error:
        print(f"已正确捕获相机异常：{type(error).__name__}: {error}")
