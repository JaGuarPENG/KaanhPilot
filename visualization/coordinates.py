"""仅用于 Open3D 窗口的坐标显示转换。"""

from __future__ import annotations

import numpy as np


def camera_optical_to_open3d_display(camera_points_m: np.ndarray) -> np.ndarray:
    """把 camera_optical_frame 转为更便于 Open3D 观察的坐标。

    项目中的真实传感器坐标保持 X 向右、Y 向下、Z 向前。Open3D 窗口中将
    Y、Z 反向，使画面符合常见的 Y 向上观察习惯。此函数只返回显示副本，
    调用方不得将其结果写回定位结果、标定数据或机器人控制输入。
    """
    return np.asarray(camera_points_m, dtype=np.float64) * np.array([1.0, -1.0, -1.0], dtype=np.float64)
