"""奥比中光官方深度滤波器链。

这里将厂商无关的 DepthProcessingConfig 映射为 pyorbbecsdk 的官方滤波器。
SDK 模块由 G305 适配器在运行时传入，防止其依赖泄漏到公共契约或调用方。
"""

from __future__ import annotations

from typing import Any

from camera.contracts.errors import CameraStreamError
from camera.contracts.models import DepthProcessingConfig, FilterParameterDescriptor


class OrbbecDepthFilterChain:
    """持久化的官方滤波链，顺序与奥比中光样例一致。"""

    def __init__(self, ob: Any, config: DepthProcessingConfig) -> None:
        self._config = config
        self._filters: list[tuple[str, Any]] = []
        if config.temporal_enabled:
            self._filters.append(("TemporalFilter", ob.TemporalFilter()))
        if config.spatial_enabled:
            spatial = ob.SpatialAdvancedFilter()
            # 仅开放已在当前 G305 schema 中验证过的两项空间滤波参数。
            spatial.set_config_value("magnitude", config.spatial_magnitude)
            spatial.set_config_value("alpha", config.spatial_alpha)
            self._filters.append(("SpatialAdvancedFilter", spatial))
        if config.hole_filling_enabled:
            hole_filling = ob.HoleFillingFilter()
            hole_filling.set_config_value("hole_filling_mode", config.hole_filling_mode)
            self._filters.append(("HoleFillingFilter", hole_filling))
        if config.minimum_depth_m is not None and config.maximum_depth_m is not None:
            threshold = ob.ThresholdFilter()
            # 官方 ThresholdFilter 参数单位为毫米，公共契约始终使用米。
            # pyorbbecsdk 2.1.1 要求整数毫米；round 可避免浮点表示误差导致截断。
            minimum_mm = int(round(config.minimum_depth_m * 1000.0))
            maximum_mm = int(round(config.maximum_depth_m * 1000.0))
            threshold.set_value_range(minimum_mm, maximum_mm)
            self._filters.append(("ThresholdFilter", threshold))

    @property
    def active_filter_names(self) -> tuple[str, ...]:
        """返回实际运行的官方滤波器名称，便于日志和诊断。"""
        return tuple(name for name, _ in self._filters)

    def parameter_descriptors(self) -> tuple[FilterParameterDescriptor, ...]:
        """读取当前 SDK 和当前设备实际支持的参数 schema。"""
        descriptors: list[FilterParameterDescriptor] = []
        for filter_name, depth_filter in self._filters:
            try:
                schemas = depth_filter.get_config_schema_vec()
            except Exception as error:
                raise CameraStreamError(f"无法读取官方滤波器 {filter_name} 的参数 schema") from error
            for schema in schemas:
                descriptors.append(
                    FilterParameterDescriptor(
                        filter_name=filter_name,
                        name=str(schema.name),
                        description=str(schema.desc),
                        value_type=self._serialize_schema_value(schema.type),
                        default_value=self._serialize_schema_value(schema.default),
                        minimum=self._serialize_schema_value(schema.min),
                        maximum=self._serialize_schema_value(schema.max),
                        step=self._serialize_schema_value(schema.step),
                    )
                )
        return tuple(descriptors)

    def process(self, depth_frame: Any) -> Any:
        """依序处理一帧深度并返回 SDK DepthFrame。"""
        filtered = depth_frame
        for name, depth_filter in self._filters:
            output = depth_filter.process(filtered)
            if output is None:
                raise CameraStreamError(f"官方滤波器 {name} 未返回深度帧")
            try:
                filtered = output.as_depth_frame()
            except AttributeError as error:
                raise CameraStreamError(f"官方滤波器 {name} 返回了非深度帧") from error
            if filtered is None:
                raise CameraStreamError(f"官方滤波器 {name} 返回了空深度帧")
        return filtered

    @staticmethod
    def _serialize_schema_value(value: Any) -> str | bool | int | float | None:
        """将 pybind 枚举和标量转为稳定的 JSON 可打印值。"""
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if hasattr(value, "name"):
            return str(value.name)
        return str(value)


if __name__ == "__main__":
    print("该模块由 OrbbecG305Camera 在运行时创建；请通过 camera_commands 配置滤波。")
