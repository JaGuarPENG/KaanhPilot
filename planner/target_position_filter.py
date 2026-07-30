"""Follower 目标位置滤波。

本模块只处理已经转换到仿真机器人基座坐标系的三维位置，单位固定为米。
它不读取相机、不解释感知状态，也不发送机器人命令，便于后续替换为卡尔曼等滤波策略。

输出: x_new = alpha * x_sample + (1 - alpha) * x_old

"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class FilterUpdate:
    """一次位置滤波的输出, 支队位置进行处理，不对姿态进行滤波。输出为三维位置

    ``position_m`` 是可被 follower 使用的基座系位置；``accepted`` 为 False
    表示输入被跳变门限拒绝，此时位置仍是上一次有效滤波结果。
    """

    position_m: tuple[float, float, float]
    accepted: bool


class TargetPositionFilter:
    """基于 EMA 的单目标位置滤波器。

    参数 ``alpha`` 是新观测的权重；``jump_threshold_m`` 是允许的最大单次
    位置变化。首个样本直接初始化滤波状态，调用 ``reset`` 后同样如此。
    """

    def __init__(self, alpha: float = 0.35, jump_threshold_m: float = 0.05) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha 必须位于 (0, 1] 区间")
        if jump_threshold_m <= 0.0:
            raise ValueError("jump_threshold_m 必须为正数")
        self.alpha = alpha
        self.jump_threshold_m = jump_threshold_m
        self._position_m: np.ndarray | None = None

    @property
    def position_m(self) -> tuple[float, float, float] | None:
        """当前滤波结果；尚未接收有效样本时返回 None。"""
        return None if self._position_m is None else tuple(float(v) for v in self._position_m)

    def reset(self) -> None:
        """清除跨目标和跨 follower 会话都不能复用的内部状态。"""
        self._position_m = None

    def update(self, position_m: tuple[float, float, float]) -> FilterUpdate:
        """提交一个基座系目标点并返回滤波后的目标点。"""
        sample = np.asarray(position_m, dtype=np.float64)
        if sample.shape != (3,) or not np.isfinite(sample).all():
            raise ValueError("position_m 必须是三个有限浮点数")
        if self._position_m is None:
            self._position_m = sample
            return FilterUpdate(tuple(float(v) for v in sample), True)
        if float(np.linalg.norm(sample - self._position_m)) > self.jump_threshold_m:
            return FilterUpdate(tuple(float(v) for v in self._position_m), False)
        self._position_m = self.alpha * sample + (1.0 - self.alpha) * self._position_m
        return FilterUpdate(tuple(float(v) for v in self._position_m), True)
