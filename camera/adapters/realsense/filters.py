"""将公共深度配置映射到 RealSense 官方处理块，不在此导入 SDK。

顺序：深度→视差→空间→时域→深度→孔洞填充→米制阈值。
不使用会改变图像尺寸的 decimation，D2C 像素对应关系保持不变。
参考：https://github.com/realsenseai/librealsense/blob/master/doc/post-processing-filters.md
"""

from __future__ import annotations

import math
from typing import Any

from camera.contracts.cam_structs import DepthProcessingConfig, FilterParameterDescriptor
from camera.contracts.errors import CameraProfileError, CameraStreamError


class RealSenseDepthFilterChain:
    """每台相机独占一个持久滤波链，保持时域滤波历史。

公共 hole_filling_mode 沿用既有契约：0=TOP，1=NEAREST，2=FAREST。
RealSense 原生 0=left，1=farthest，2=nearest，故映射 1→2、2→1；
TOP 没有等价模式，启用孔洞填充时拒绝 0，不能静默替换为 left。
"""

    def __init__(self, rs: Any, config: DepthProcessingConfig) -> None:
        self._filters: list[tuple[str, Any]] = []
        if config.hole_filling_enabled and config.hole_filling_mode == 0:
            raise CameraProfileError("D435 不支持 TOP 孔洞填充；请使用 1（NEAREST）或 2（FAREST）")
        try:
            stereo_filtering = config.spatial_enabled or config.temporal_enabled
            if stereo_filtering:
                self._filters.append(("DepthToDisparity", rs.disparity_transform(True)))
            if config.spatial_enabled:
                spatial = rs.spatial_filter()
                self._set_option(spatial, rs.option.filter_magnitude, config.spatial_magnitude)
                self._set_option(spatial, rs.option.filter_smooth_alpha, config.spatial_alpha)
                self._filters.append(("SpatialFilter", spatial))
            if config.temporal_enabled:
                self._filters.append(("TemporalFilter", rs.temporal_filter()))
            if stereo_filtering:
                self._filters.append(("DisparityToDepth", rs.disparity_transform(False)))
            if config.hole_filling_enabled:
                hole = rs.hole_filling_filter()
                self._set_option(hole, rs.option.holes_fill, {1: 2, 2: 1}[config.hole_filling_mode])
                self._filters.append(("HoleFillingFilter", hole))
            if config.minimum_depth_m is not None:
                threshold = rs.threshold_filter()
                # RealSense min_distance/max_distance 原生单位就是米。
                self._set_option(threshold, rs.option.min_distance, config.minimum_depth_m)
                self._set_option(threshold, rs.option.max_distance, config.maximum_depth_m)
                self._filters.append(("ThresholdFilter", threshold))
        except CameraProfileError:
            raise
        except Exception as error:
            raise CameraProfileError(f"无法创建 D435 深度滤波链：{error}") from error

    @staticmethod
    def _set_option(depth_filter: Any, option: Any, value: float) -> None:
        # processing_block.supports() 查询 camera_info，不接受 rs.option。
        if option not in depth_filter.get_supported_options():
            raise CameraProfileError(f"RealSense 滤波器不支持参数 {option}")
        limits = depth_filter.get_option_range(option)
        if not math.isfinite(value) or not limits.min <= value <= limits.max:
            raise CameraProfileError(f"RealSense 参数 {option}={value} 超出 [{limits.min}, {limits.max}]")
        # SDK 的浮点参数 step 不一定是强制量化间隔（如阈值允许 0.15m）。
        # 保留范围检查，其余约束交由对应处理块的 set_option 校验。
        depth_filter.set_option(option, float(value))

    @property
    def active_filter_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._filters)

    def parameter_descriptors(self) -> tuple[FilterParameterDescriptor, ...]:
        """返回 SDK 原生参数描述（holes_fill 的值使用 SDK 原生语义）。"""
        descriptors = []
        try:
            for name, depth_filter in self._filters:
                for option in depth_filter.get_supported_options():
                    limits = depth_filter.get_option_range(option)
                    descriptors.append(FilterParameterDescriptor(
                        filter_name=name, name=str(option),
                        description=depth_filter.get_option_description(option),
                        value_type="float", default_value=float(limits.default),
                        minimum=float(limits.min), maximum=float(limits.max), step=float(limits.step),
                    ))
        except Exception as error:
            raise CameraStreamError("无法读取 RealSense 滤波器参数 schema") from error
        return tuple(descriptors)

    def process(self, depth_frame: Any) -> Any:
        filtered = depth_frame
        try:
            for name, depth_filter in self._filters:
                filtered = depth_filter.process(filtered)
                if not filtered:
                    raise CameraStreamError(f"RealSense 滤波器 {name} 返回空帧")
            # 中间输出可能是视差帧，只在完整链末尾要求恢复为深度帧。
            if self._filters:
                filtered = filtered.as_depth_frame()
            if not filtered:
                raise CameraStreamError("RealSense 滤波链未返回深度帧")
        except CameraStreamError:
            raise
        except Exception as error:
            raise CameraStreamError("RealSense 深度滤波失败") from error
        return filtered
