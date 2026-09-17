"""Gemini 305（G305）奥比中光相机适配器。

这是项目中唯一允许接触 ``pyorbbecsdk`` 的运行时模块。它独占 SDK Pipeline，
在后台线程中持续覆盖最新观测，消费者只能获得厂商无关的数据对象。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import cv2
import numpy as np

from camera.contracts.errors import CameraError, CameraNotFoundError, CameraProfileError, CameraStateError, CameraStreamError, CameraTimeoutError
from camera.contracts.interface import Camera
from camera.contracts.cam_structs import (
    AlignedRGBDObservation,
    AlignmentMode,
    CameraCapabilities,
    CameraDistortion,
    CameraIntrinsics,
    CameraProfile,
    CameraState,
    DepthProcessingConfig,
    FilterParameterDescriptor,
    RigidTransform,
    SensorCalibration,
)
from camera.adapters.orbbec.filters import OrbbecDepthFilterChain
from camera.adapters.orbbec.profiles import G305_1280X800_30, G305_848X480_30, G305_SUPPORTED_PROFILES


_LOGGER = logging.getLogger(__name__)

_RECOVERING_AFTER_S = 5.0
_REBUILD_AFTER_S = 10.0
_MAX_REBUILD_ATTEMPTS = 3
_REBUILD_BACKOFF_S = (0.0, 1.0, 2.0)
_RECOVERY_STABLE_FRAME_COUNT = 3
_REBUILD_FIRST_FRAME_TIMEOUT_S = 10.0
_STARTUP_WAIT_GRACE_S = 0.5
_THREAD_STOP_TIMEOUT_S = 15.0


class OrbbecG305Camera(Camera):
    """使用第 ``device_index`` 台 Gemini 305 的独占 RGB-D 相机流。

    优先使用软件 D2C 对齐，若请求硬件 D2C 且设备支持则使用硬件 D2C。目前只有848X480_30的profile支持硬件D2C。

    硬件 D2C 仅在预设的848x480分辨率下可用。

    参数表：
    - ``profile``：请求的相机 Profile，必须在 G305_SUPPORTED_PROFILES 中。
    - ``alignment_mode``：请求的 D2C 对齐模式，默认 SOFTWARE。
    - ``device_index``：选择第几台相机，默认 0。
    - ``frame_timeout_ms``：等待一帧的最长毫秒数，默认 1000。
    - ``startup_timeout_s``：启动等待首帧的最长秒数，默认 8.0。
    - ``depth_processing``：深度处理配置，默认使用官方 SDK 的滤波链。
    - ``observation_mode``：观测模式，FINAL_ONLY 或 RAW_AND_FILTERED, 其中RAW_AND_FILTERED模式会在每帧观测中同时返回未过滤和过滤后的深度图和点云，用于滤波诊断。

    """

    def __init__(
        self,
        profile: CameraProfile,
        alignment_mode: AlignmentMode = AlignmentMode.SOFTWARE,
        device_index: int = 0,
        frame_timeout_ms: int = 1_000,
        startup_timeout_s: float = 8.0,
        depth_processing: DepthProcessingConfig = DepthProcessingConfig(),
        observation_mode: str = "FINAL_ONLY"
    ) -> None:
        if device_index < 0:
            raise ValueError("设备索引不能为负数")
        if frame_timeout_ms <= 0 or startup_timeout_s <= 0:
            raise ValueError("超时时间必须为正数")
        self._requested_profile = profile #请求的 profile
        self._requested_alignment = alignment_mode #请求的对齐模式
        self._device_index = device_index #设备id
        self._frame_timeout_ms = frame_timeout_ms #一帧最长等待时间
        self._startup_timeout_s = startup_timeout_s #启动超时时间
        self._depth_processing = depth_processing #深度处理配置

        self._lock = threading.RLock()
        self._state = CameraState.STOPPED
        self._camera_id: str | None = None
        self._actual_profile: CameraProfile | None = None
        self._actual_alignment: AlignmentMode | None = None
        self._calibration: SensorCalibration | None = None
        self._latest_observation: AlignedRGBDObservation | None = None
        self._latest_unfiltered_observation: AlignedRGBDObservation | None = None
        self._last_error: CameraError | None = None
        self._stream_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._frame_id = 0
        self._ray_x: np.ndarray | None = None
        self._ray_y: np.ndarray | None = None
        self._context: Any | None = None
        self._align_filter: Any | None = None
        self._depth_filter_chain: OrbbecDepthFilterChain | None = None
        self._observation_mode = observation_mode
        if self._observation_mode not in ("FINAL_ONLY", "RAW_AND_FINAL"):
            raise ValueError("observation_mode 必须为 FINAL_ONLY 或 RAW_AND_FINAL") 


        self._validate_requested_profile()

    @property
    def state(self) -> CameraState:
        with self._lock:
            return self._state

    @property
    def camera_id(self) -> str | None:
        with self._lock:
            return self._camera_id

    @property
    def actual_profile(self) -> CameraProfile | None:
        """启动成功后返回实际接受的 Profile。"""
        with self._lock:
            return self._actual_profile

    @property
    def actual_alignment_mode(self) -> AlignmentMode | None:
        """启动成功后返回硬件或软件的实际 D2C 路径。"""
        with self._lock:
            return self._actual_alignment

    @property
    def calibration(self) -> SensorCalibration | None:
        """启动成功后返回与实际 Profile 匹配的标定。"""
        with self._lock:
            return self._calibration

    @property
    def depth_processing(self) -> DepthProcessingConfig:
        """返回当前实例请求并写入每帧观测的深度处理配置。"""
        return self._depth_processing
    
    def set_observation_mode(self, mode: str) -> None:
        """设置观测模式。"""
        if mode not in ("FINAL_ONLY", "RAW_AND_FILTERED"):
            raise ValueError("observation_mode 必须为 FINAL_ONLY 或 RAW_AND_FILTERED")
        self._observation_mode = mode
        return True

    def get_depth_filter_parameter_schemas(self) -> tuple[FilterParameterDescriptor, ...]:
        """返回当前已启动官方滤波链支持的参数描述，仅用于诊断和配置设计。"""
        with self._lock:
            if self._state == CameraState.FAILED and self._last_error is not None:
                raise self._last_error
            if self._depth_filter_chain is None:
                raise CameraStateError("相机尚未启动，无法读取滤波参数 schema")
            return self._depth_filter_chain.parameter_descriptors()

    
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            camera_id=self._camera_id or f"G305-{self._device_index}",
            supported_profiles=G305_SUPPORTED_PROFILES,
            software_alignment_profiles=G305_SUPPORTED_PROFILES,
            hardware_alignment_profiles=(G305_848X480_30,) if G305_848X480_30 in G305_SUPPORTED_PROFILES else (),
        )

    def start(self) -> None:
        """启动后台流，并等待首帧以保证调用方可立即读取标定与观测。"""
        with self._lock:
            if self._state == CameraState.CLOSED:
                raise CameraStateError("相机已关闭，不能再次启动")
            if self._state in (CameraState.STARTING, CameraState.STREAMING, CameraState.RECOVERING):
                raise CameraStateError("相机已经启动")
            if self._state == CameraState.FAILED:
                raise CameraStateError("相机已失败，请创建新实例而非自动重连")
            self._state = CameraState.STARTING
            self._last_error = None
            self._latest_observation = None
            self._latest_unfiltered_observation = None
            self._frame_id = 0
            self._ray_x = None
            self._ray_y = None
            self._stop_event.clear()
            self._ready_event.clear()
            self._stream_thread = threading.Thread(target=self._stream_main, name="orbbec-g305-camera", daemon=True)
            self._stream_thread.start()

        # 后台线程按 frame_timeout_ms 分段等待，允许最后一次等待完整返回后再报告启动结果。
        startup_wait_s = self._startup_timeout_s + self._frame_timeout_ms / 1000.0 + _STARTUP_WAIT_GRACE_S
        if not self._ready_event.wait(startup_wait_s):
            self.stop()
            raise CameraTimeoutError(f"相机在 {self._startup_timeout_s:.1f} 秒内未发布首帧")
        with self._lock:
            if self._last_error is not None:
                raise self._last_error

    def stop(self) -> None:
        """请求后台线程停止；奥比中光相机 SDK Pipeline 只由该线程创建和销毁。"""
        with self._lock:
            if self._state == CameraState.CLOSED:
                return
            thread = self._stream_thread
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            # 除 wait_for_frames 外，SDK 停止 Color/Depth 流也可能需要数秒。
            thread.join(max(self._frame_timeout_ms / 1000.0 + 2.0, _THREAD_STOP_TIMEOUT_S))
        with self._lock:
            if (thread is None or not thread.is_alive()) and self._state not in (CameraState.FAILED, CameraState.CLOSED):
                self._state = CameraState.STOPPED

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._state = CameraState.CLOSED

    def get_latest_observation(self) -> AlignedRGBDObservation | None:
        with self._lock:
            if self._state == CameraState.FAILED and self._last_error is not None:
                raise self._last_error
            return self._latest_observation

    def get_latest_filter_comparison_observations(self) -> tuple[AlignedRGBDObservation, AlignedRGBDObservation] | None:
        """读取同一 frame_id 的（未过滤，过滤后）RGBD图像观测。"""
        with self._lock:
            if self._observation_mode != "RAW_AND_FILTERED":
                raise CameraStateError("相机未设置 RAW_AND_FILTERED 模式，无法获取滤波对比观测")
            if self._state == CameraState.FAILED and self._last_error is not None:
                raise self._last_error
            if self._latest_unfiltered_observation is None or self._latest_observation is None:
                raise CameraStateError("相机尚未发布滤波对比观测")
            return self._latest_unfiltered_observation, self._latest_observation

    def _stream_main(self) -> None:
        pipeline: Any | None = None
        try:
            ob = self._load_sdk()
            pipeline, actual_alignment = self._open_pipeline_session(ob)
            stable_result = self._wait_for_stable_observations(
                pipeline,
                ob,
                actual_alignment,
                required_count=1,
                timeout_s=self._startup_timeout_s,
            )
            if stable_result is None:
                return
            calibration, raw_observation, observation = stable_result
            self._publish_streaming_observation(raw_observation, observation, actual_alignment, calibration)
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    self._consume_pipeline_session(pipeline, ob, actual_alignment, calibration)
                    break
                except CameraProfileError:
                    raise
                except Exception as error:
                    if self._stop_event.is_set():
                        break
                    self._enter_recovering(error)
                    self._close_pipeline_session(pipeline)
                    pipeline = None

                    recovery_result = self._rebuild_pipeline(ob, error)
                    if recovery_result is None:
                        break
                    pipeline, actual_alignment, calibration = recovery_result

        except Exception as error:
            if not self._stop_event.is_set():
                camera_error = error if isinstance(error, CameraError) else CameraStreamError(str(error))
                with self._lock:
                    self._last_error = camera_error
                    self._state = CameraState.FAILED
                    self._ready_event.set()
        finally:
            if pipeline is not None:
                self._close_pipeline_session(pipeline)
            with self._lock:
                if self._stop_event.is_set() and self._state not in (CameraState.FAILED, CameraState.CLOSED):
                    self._state = CameraState.STOPPED
                elif self._state == CameraState.STARTING:
                    self._state = CameraState.STOPPED
                    self._ready_event.set()
                if self._stream_thread is threading.current_thread():
                    self._stream_thread = None

    def _open_pipeline_session(self, ob: Any) -> tuple[Any, AlignmentMode]:
        """创建并启动一套全新的 SDK Pipeline；失败时释放本次创建的资源。"""
        pipeline: Any | None = None
        try:
            device = self._select_device(ob)
            pipeline = self._create_pipeline(ob, device)
            config, actual_alignment = self._build_config(ob, pipeline)
            self._align_filter = None
            if actual_alignment == AlignmentMode.SOFTWARE:
                self._align_filter = ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
            pipeline.start(config)

            # 官方样例要求 Pipeline 成功启动后再创建滤波器，部分 SDK 版本会在
            # 没有活动设备上下文时拒绝构造滤波对象。
            self._depth_filter_chain = OrbbecDepthFilterChain(ob, self._depth_processing)
            return pipeline, actual_alignment
        except Exception:
            if pipeline is not None:
                try:
                    pipeline.stop()
                except Exception:
                    pass
            self._clear_pipeline_session_references()
            raise

    def _close_pipeline_session(self, pipeline: Any) -> None:
        """停止一套 Pipeline 并释放只能在采集线程中持有的 SDK 对象。"""
        try:
            pipeline.stop()
        except Exception as error:
            _LOGGER.warning("停止 G305 Pipeline 时发生异常: %s", error)
        finally:
            self._clear_pipeline_session_references()

    def _clear_pipeline_session_references(self) -> None:
        with self._lock:
            self._align_filter = None
            self._depth_filter_chain = None
            self._context = None

    def _consume_pipeline_session(
        self,
        pipeline: Any,
        ob: Any,
        actual_alignment: AlignmentMode,
        calibration: SensorCalibration,
    ) -> None:
        """消费当前 Pipeline，短时断流保留旧帧，恢复阶段验证连续三帧。"""
        last_published_at = time.monotonic()
        stable_frame_count = 0

        while not self._stop_event.is_set():
            frames = self._wait_complete_frames(pipeline, ob, actual_alignment)
            if self._stop_event.is_set():
                return
            now = time.monotonic()

            with self._lock:
                recovering = self._state == CameraState.RECOVERING

            if recovering and now - last_published_at >= _REBUILD_AFTER_S:
                raise CameraTimeoutError(f"连续 {_REBUILD_AFTER_S:.0f} 秒未恢复稳定 RGB-D 帧，准备重建 Pipeline")

            if frames is None:
                stable_frame_count = 0
                elapsed_s = now - last_published_at
                if not recovering and elapsed_s >= _RECOVERING_AFTER_S:
                    self._enter_recovering(
                        CameraTimeoutError(f"连续 {_RECOVERING_AFTER_S:.0f} 秒未收到完整 RGB-D 帧")
                    )
                continue

            raw_observation, observation = self._make_observations(frames, ob, actual_alignment, calibration)
            if recovering:
                stable_frame_count += 1
                if stable_frame_count < _RECOVERY_STABLE_FRAME_COUNT:
                    continue
                self._publish_streaming_observation(raw_observation, observation, actual_alignment, calibration)
                last_published_at = time.monotonic()
                stable_frame_count = 0
                _LOGGER.info("G305 已连续收到 %d 帧，恢复 STREAMING", _RECOVERY_STABLE_FRAME_COUNT)
                continue

            self._publish_observation(raw_observation, observation)
            last_published_at = time.monotonic()

    def _wait_for_stable_observations(
        self,
        pipeline: Any,
        ob: Any,
        actual_alignment: AlignmentMode,
        required_count: int,
        timeout_s: float,
    ) -> tuple[SensorCalibration, AlignedRGBDObservation, AlignedRGBDObservation] | None:
        """等待连续完整且可处理的观测；验证帧在达到要求前不对外发布。"""
        deadline = time.monotonic() + timeout_s
        calibration: SensorCalibration | None = None
        consecutive_count = 0

        while not self._stop_event.is_set() and time.monotonic() < deadline:
            frames = self._wait_complete_frames(pipeline, ob, actual_alignment)
            if self._stop_event.is_set():
                return None
            if time.monotonic() >= deadline:
                break
            if frames is None:
                consecutive_count = 0
                continue
            if calibration is None:
                calibration = self._read_calibration(pipeline)
            raw_observation, observation = self._make_observations(frames, ob, actual_alignment, calibration)
            consecutive_count += 1
            if consecutive_count >= required_count:
                return calibration, raw_observation, observation

        if self._stop_event.is_set():
            return None
        raise CameraTimeoutError(
            f"在 {timeout_s:.1f} 秒内未连续收到 {required_count} 套完整 RGB-D 帧"
        )

    def _rebuild_pipeline(
        self,
        ob: Any,
        initial_error: Exception,
    ) -> tuple[Any, AlignmentMode, SensorCalibration] | None:
        """最多重建三次 Pipeline，连续三帧成功后才重新发布。"""
        last_error: Exception = initial_error

        for attempt_index in range(_MAX_REBUILD_ATTEMPTS):
            backoff_s = _REBUILD_BACKOFF_S[attempt_index]
            if backoff_s > 0 and self._stop_event.wait(backoff_s):
                return None
            if self._stop_event.is_set():
                return None

            pipeline: Any | None = None
            try:
                _LOGGER.warning(
                    "正在重建 G305 Pipeline（第 %d/%d 次）",
                    attempt_index + 1,
                    _MAX_REBUILD_ATTEMPTS,
                )
                pipeline, actual_alignment = self._open_pipeline_session(ob)
                stable_result = self._wait_for_stable_observations(
                    pipeline,
                    ob,
                    actual_alignment,
                    required_count=_RECOVERY_STABLE_FRAME_COUNT,
                    timeout_s=_REBUILD_FIRST_FRAME_TIMEOUT_S,
                )
                if stable_result is None:
                    self._close_pipeline_session(pipeline)
                    return None
                calibration, raw_observation, observation = stable_result
                self._publish_streaming_observation(raw_observation, observation, actual_alignment, calibration)
                _LOGGER.info("G305 Pipeline 重建成功")
                return pipeline, actual_alignment, calibration
            except CameraProfileError:
                if pipeline is not None:
                    self._close_pipeline_session(pipeline)
                raise
            except Exception as error:
                last_error = error
                if pipeline is not None:
                    self._close_pipeline_session(pipeline)
                _LOGGER.warning(
                    "G305 Pipeline 第 %d/%d 次重建失败: %s",
                    attempt_index + 1,
                    _MAX_REBUILD_ATTEMPTS,
                    error,
                )

        raise CameraStreamError(
            f"G305 Pipeline 连续 {_MAX_REBUILD_ATTEMPTS} 次重建失败；最后错误: {last_error}"
        ) from last_error

    def _enter_recovering(self, error: Exception) -> None:
        """进入恢复态并清除旧帧，防止恢复阶段继续暴露过期画面。"""
        camera_error = error if isinstance(error, CameraError) else CameraStreamError(str(error))
        with self._lock:
            if self._state != CameraState.RECOVERING:
                _LOGGER.warning("G305 进入 RECOVERING: %s", camera_error)
            self._state = CameraState.RECOVERING
            self._last_error = camera_error
            self._latest_observation = None
            self._latest_unfiltered_observation = None

    def _publish_streaming_observation(
        self,
        raw_observation: AlignedRGBDObservation,
        observation: AlignedRGBDObservation,
        actual_alignment: AlignmentMode,
        calibration: SensorCalibration,
    ) -> None:
        with self._lock:
            self._actual_profile = self._requested_profile
            self._actual_alignment = actual_alignment
            self._calibration = calibration
            self._last_error = None
            self._state = CameraState.STREAMING
            self._publish_observation_locked(raw_observation, observation)

    def _publish_observation(
        self,
        raw_observation: AlignedRGBDObservation,
        observation: AlignedRGBDObservation,
    ) -> None:
        with self._lock:
            self._publish_observation_locked(raw_observation, observation)

    def _publish_observation_locked(
        self,
        raw_observation: AlignedRGBDObservation,
        observation: AlignedRGBDObservation,
    ) -> None:
        self._latest_observation = observation
        self._latest_unfiltered_observation = (
            None if self._observation_mode == "FINAL_ONLY" else raw_observation
        )

    @staticmethod
    def _load_sdk() -> Any:
        try:
            import pyorbbecsdk as ob
            ob.Context.set_logger_to_console(ob.OBLogLevel.WARNING)
        except ImportError as error:
            raise CameraStreamError("无法导入 pyorbbecsdk；请在 pyagent 环境中运行") from error
        return ob

    def _select_device(self, ob: Any) -> Any:
        context = ob.Context()
        devices = context.query_devices()
        count = devices.get_count()
        with self._lock:
            expected_camera_id = self._camera_id

        device: Any | None = None
        if expected_camera_id is not None:
            # 重连时按首次启动记录的序列号找回同一台设备，避免 USB 重枚举后索引改变。
            for index in range(count):
                candidate = devices[index]
                if candidate.get_device_info().get_serial_number() == expected_camera_id:
                    device = candidate
                    break
            if device is None:
                raise CameraNotFoundError(f"未检测到原相机 {expected_camera_id}")
        else:
            if self._device_index >= count:
                raise CameraNotFoundError(f"请求设备索引 {self._device_index}，但当前只检测到 {count} 台相机")
            device = devices[self._device_index]

        device_info = device.get_device_info()
        serial_number = device_info.get_serial_number()
        if not serial_number:
            raise CameraStreamError("设备未返回稳定序列号，不能作为 camera_id")
        with self._lock:
            self._camera_id = serial_number
            # 保留 Context 生命周期，避免部分 SDK 版本提前释放 Device 句柄。
            self._context = context
        return device

    @staticmethod
    def _create_pipeline(ob: Any, device: Any) -> Any:
        try:
            return ob.Pipeline(device)
        except TypeError as error:
            # 选择第 0 台以外设备必须被 SDK 明确支持，不能静默退回默认设备。
            raise CameraStreamError("当前 pyorbbecsdk 不支持按设备对象创建 Pipeline") from error

    def _build_config(self, ob: Any, pipeline: Any) -> tuple[Any, AlignmentMode]:
        if self._requested_alignment in (AlignmentMode.HARDWARE, AlignmentMode.AUTO):
            try:
                return self._build_hardware_config(ob, pipeline), AlignmentMode.HARDWARE
            except CameraProfileError:
                if self._requested_alignment == AlignmentMode.HARDWARE:
                    raise
        return self._build_software_config(ob, pipeline), AlignmentMode.SOFTWARE

    def _build_software_config(self, ob: Any, pipeline: Any) -> Any:
        config = ob.Config()
        color = self._find_profile(ob, pipeline, ob.OBSensorType.COLOR_SENSOR, self._requested_profile.color_width, self._requested_profile.color_height, self._requested_profile.color_fps, self._requested_profile.color_format)
        depth = self._find_profile(ob, pipeline, ob.OBSensorType.DEPTH_SENSOR, self._requested_profile.depth_width, self._requested_profile.depth_height, self._requested_profile.depth_fps, self._requested_profile.depth_format)
        config.enable_stream(color)
        config.enable_stream(depth)
        config.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
        return config

    def _build_hardware_config(self, ob: Any, pipeline: Any) -> Any:
        color = self._find_profile(ob, pipeline, ob.OBSensorType.COLOR_SENSOR, self._requested_profile.color_width, self._requested_profile.color_height, self._requested_profile.color_fps, self._requested_profile.color_format)
        candidates = pipeline.get_d2c_depth_profile_list(color, ob.OBAlignMode.HW_MODE)
        for index in range(len(candidates)):
            depth = candidates[index]
            if self._profile_matches(depth, self._requested_profile.depth_width, self._requested_profile.depth_height, self._requested_profile.depth_fps, self._requested_profile.depth_format):
                config = ob.Config()
                config.enable_stream(depth)
                config.enable_stream(color)
                config.set_align_mode(ob.OBAlignMode.HW_MODE)
                # 只接受同一帧集中同时到达的 RGB 与深度，不能把独立帧拼成观测。
                config.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
                return config
        raise CameraProfileError("该设备不支持请求 Profile 的硬件 D2C 对齐")

    @staticmethod
    def _find_profile(ob: Any, pipeline: Any, sensor_type: Any, width: int, height: int, fps: int, format_name: str) -> Any:
        format_value = getattr(ob.OBFormat, format_name, None)
        if format_value is None:
            raise CameraProfileError(f"pyorbbecsdk 不存在像素格式 {format_name}")
        try:
            profiles = pipeline.get_stream_profile_list(sensor_type)
            profile = profiles.get_video_stream_profile(width, height, format_value, fps)
        except Exception as error:
            raise CameraProfileError(f"无法查找 {width}x{height}@{fps} {format_name} 流") from error
        if profile is None or not OrbbecG305Camera._profile_matches(profile, width, height, fps, format_name):
            raise CameraProfileError(f"设备不支持精确流配置 {width}x{height}@{fps} {format_name}")
        return profile

    @staticmethod
    def _profile_matches(profile: Any, width: int, height: int, fps: int, format_name: str) -> bool:
        return (
            profile.get_width() == width
            and profile.get_height() == height
            and profile.get_fps() == fps
            and getattr(profile.get_format(), "name", str(profile.get_format())) == format_name
        )

    def _wait_complete_frames(self, pipeline: Any, ob: Any, alignment: AlignmentMode) -> Any | None:
        frames = pipeline.wait_for_frames(self._frame_timeout_ms)
        if frames is None:
            return None
        if alignment == AlignmentMode.SOFTWARE:
            if self._align_filter is None:
                raise CameraStreamError("软件 D2C 过滤器尚未初始化")
            frames = self._align_filter.process(frames)
        if frames is None or frames.get_color_frame() is None or frames.get_depth_frame() is None:
            return None
        return frames

    @staticmethod
    def _read_calibration(pipeline: Any) -> SensorCalibration:
        params = pipeline.get_camera_param()

        def intrinsic(value: Any) -> CameraIntrinsics:
            return CameraIntrinsics(value.width, value.height, value.fx, value.fy, value.cx, value.cy)

        def distortion(value: Any) -> CameraDistortion:
            return CameraDistortion(value.k1, value.k2, value.k3, value.p1, value.p2)

        transform = params.transform
        return SensorCalibration(
            rgb_intrinsics=intrinsic(params.rgb_intrinsic),
            depth_intrinsics=intrinsic(params.depth_intrinsic),
            rgb_distortion=distortion(params.rgb_distortion),
            depth_distortion=distortion(params.depth_distortion),
            # 官方 SDK 外参平移单位为毫米，公共契约统一使用米。
            depth_to_rgb=RigidTransform(np.asarray(transform.rot).reshape(3, 3), np.asarray(transform.transform, dtype=np.float64) / 1000.0),
        )

    def _make_observations(self, frames: Any, ob: Any, alignment: AlignmentMode, calibration: SensorCalibration) -> tuple[AlignedRGBDObservation, AlignedRGBDObservation]:
        """从同一套对齐帧生成未过滤与过滤后观测，用于官方滤波诊断。"""
        color_frame = frames.get_color_frame()
        raw_depth_frame = frames.get_depth_frame()
        if raw_depth_frame is None:
            raise CameraStreamError("帧集缺少原始深度帧")
        if self._depth_filter_chain is not None:
            # D2C 已完成后滤波，确保最终深度和点云始终处在 RGB 像素坐标系。
            filtered_depth_frame = self._depth_filter_chain.process(raw_depth_frame)
        else:
            filtered_depth_frame = raw_depth_frame
        rgb = self._frame_to_rgb(color_frame, ob)
        raw_depth_m = self._depth_frame_to_m(raw_depth_frame)
        filtered_depth_m = self._depth_frame_to_m(filtered_depth_frame)
        if raw_depth_m.shape != rgb.shape[:2] or filtered_depth_m.shape != rgb.shape[:2]:
            raise CameraStreamError("D2C 后的深度图尺寸仍与 RGB 图不一致")
        raw_point_cloud = self._build_organized_point_cloud(raw_depth_m, calibration.rgb_intrinsics)
        filtered_point_cloud = self._build_organized_point_cloud(filtered_depth_m, calibration.rgb_intrinsics)
        timestamp = int(color_frame.get_timestamp())
        if timestamp < 0:
            raise CameraStreamError("相机未提供有效的硬件采集时间戳")
        self._frame_id += 1
        if self._observation_mode == "FINAL_ONLY":
            observation = AlignedRGBDObservation(self._frame_id, timestamp, rgb, filtered_depth_m, filtered_point_cloud, self._requested_profile, alignment, calibration, self._depth_processing)
            return observation, observation
        else:
            raw_observation = AlignedRGBDObservation(self._frame_id, timestamp, rgb, raw_depth_m, raw_point_cloud, self._requested_profile, alignment, calibration, DepthProcessingConfig())
            filtered_observation = AlignedRGBDObservation(self._frame_id, timestamp, rgb, filtered_depth_m, filtered_point_cloud, self._requested_profile, alignment, calibration, self._depth_processing)
            return raw_observation, filtered_observation

    @staticmethod
    def _depth_frame_to_m(depth_frame: Any) -> np.ndarray:
        """将 SDK 的 Y16 深度帧转换为米单位 float32 数组。"""
        try:
            raw_depth = np.frombuffer(depth_frame.get_data(), dtype=np.uint16).reshape(depth_frame.get_height(), depth_frame.get_width())
        except ValueError as error:
            raise CameraStreamError("深度帧字节数与声明尺寸不一致") from error
        return raw_depth.astype(np.float32) * np.float32(depth_frame.get_depth_scale() / 1000.0)

    @staticmethod
    def _frame_to_rgb(frame: Any, ob: Any) -> np.ndarray:
        data = np.frombuffer(frame.get_data(), dtype=np.uint8)
        frame_format = getattr(frame.get_format(), "name", str(frame.get_format()))
        if frame_format == "MJPG":
            bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if bgr is None:
                raise CameraStreamError("无法解码 MJPG 彩色帧")
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).copy()
        height, width = frame.get_height(), frame.get_width()
        if frame_format == "RGB":
            return data.reshape(height, width, 3).copy()
        if frame_format == "BGR":
            return cv2.cvtColor(data.reshape(height, width, 3), cv2.COLOR_BGR2RGB).copy()
        raise CameraStreamError(f"不支持的彩色帧格式 {frame_format}")

    def _build_organized_point_cloud(self, depth_m: np.ndarray, intrinsics: CameraIntrinsics) -> np.ndarray:
        height, width = depth_m.shape
        if (self._ray_x is None or self._ray_x.shape != depth_m.shape or self._ray_y is None or self._ray_y.shape != depth_m.shape):
            columns, rows = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
            self._ray_x = (columns - intrinsics.cx) / intrinsics.fx
            self._ray_y = (rows - intrinsics.cy) / intrinsics.fy
        valid = np.isfinite(depth_m) & (depth_m > 0.0)
        point_cloud = np.full((height, width, 3), np.nan, dtype=np.float32)
        point_cloud[..., 0][valid] = self._ray_x[valid] * depth_m[valid]
        point_cloud[..., 1][valid] = self._ray_y[valid] * depth_m[valid]
        point_cloud[..., 2][valid] = depth_m[valid]
        return point_cloud
    

    def _validate_requested_profile(self) -> None:
        if self._requested_profile not in G305_SUPPORTED_PROFILES:
            raise CameraProfileError(f"请求的 Profile {self._requested_profile} 不在 G305 支持列表中")
        if self._requested_profile != G305_848X480_30 and self._requested_alignment == AlignmentMode.HARDWARE:
            raise CameraProfileError("仅有 848x480@30 的 Profile 支持硬件 D2C 对齐")
