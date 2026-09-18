"""RealSense D435 适配器；仅在启动时加载 pyrealsense2。

沿用 G305 的独占后台 Pipeline、首帧等待、只读最新观测结构。
AUTO/SOFTWARE 均使用 SDK rs.align(color)，不声明硬件 D2C 能力。
厂商帧、Pipeline 和处理块均不向消费者暴露。
"""

from __future__ import annotations

import math
import logging
import threading
import time
from typing import Any

import numpy as np

from camera.adapters.realsense.filters import RealSenseDepthFilterChain
from camera.adapters.realsense.profiles import D435_SUPPORTED_PROFILES
from camera.contracts.cam_structs import (
    AlignedRGBDObservation, AlignmentMode, CameraCapabilities, CameraDistortion,
    CameraIntrinsics, CameraProfile, CameraState, DepthProcessingConfig,
    FilterParameterDescriptor, RigidTransform, SensorCalibration, RGBFrame
)
from camera.adapters.latest_capture import CapturedFrames, LatestCapture
from camera.contracts.errors import (
    CameraError, CameraNotFoundError, CameraProfileError, CameraStateError,
    CameraStreamError, CameraTimeoutError,
)
from camera.contracts.interface import Camera


_LOGGER = logging.getLogger(__name__)

# 与 G305 保持一致的断流恢复策略。
_RECOVERING_AFTER_S = 5.0
_REBUILD_AFTER_S = 10.0
_MAX_REBUILD_ATTEMPTS = 3
_REBUILD_BACKOFF_S = (0.0, 1.0, 2.0)
_RECOVERY_STABLE_FRAME_COUNT = 3
_REBUILD_FIRST_FRAME_TIMEOUT_S = 10.0
_STARTUP_WAIT_GRACE_S = 0.5
_THREAD_STOP_TIMEOUT_S = 15.0


class RealSenseD435Camera(Camera):
    """按 D435 设备索引选择相机，也可用 serial_number 绑定物理设备。

    profile 必须来自 D435_SUPPORTED_PROFILES。start 等待首个完整观测；
    stop 可后续重启，RECOVERING 自动恢复，重试耗尽后 FAILED 为终止状态，
    close 永久关闭。滤波默认关闭。
    observation_mode 为 FINAL_ONLY 或 RAW_AND_FILTERED，后者额外保留
    同帧未过滤观测。数组由适配器拥有，不依赖 SDK 帧缓冲生命周期。
    """

    def __init__(
        self,
        profile: CameraProfile,
        alignment_mode: AlignmentMode = AlignmentMode.SOFTWARE,
        device_index: int = 0,
        frame_timeout_ms: int = 1_000,
        startup_timeout_s: float = 8.0,
        depth_processing: DepthProcessingConfig = DepthProcessingConfig(),
        observation_mode: str = "FINAL_ONLY",
        *,
        serial_number: str | None = None,
    ) -> None:
        if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
            raise ValueError("设备索引必须为非负整数")
        if isinstance(frame_timeout_ms, bool) or not isinstance(frame_timeout_ms, int) or frame_timeout_ms <= 0:
            raise ValueError("帧超时必须为正整数毫秒")
        if not math.isfinite(startup_timeout_s) or startup_timeout_s <= 0:
            raise ValueError("启动超时必须为有限正数")
        if serial_number is not None and not serial_number.strip():
            raise ValueError("设备序列号不能为空")
        if profile not in D435_SUPPORTED_PROFILES:
            raise CameraProfileError(f"请求的 Profile {profile} 不在 D435 支持列表中")
        alignment_mode = AlignmentMode(alignment_mode)
        if alignment_mode == AlignmentMode.HARDWARE:
            raise CameraProfileError("D435 适配器只支持软件 D2C；请使用 SOFTWARE 或 AUTO")
        if depth_processing.hole_filling_enabled and depth_processing.hole_filling_mode == 0:
            raise CameraProfileError("D435 不支持 TOP 孔洞填充；请使用 1（NEAREST）或 2（FAREST）")
        self._requested_profile = profile
        self._requested_alignment = alignment_mode
        self._device_index = device_index
        self._serial_number = serial_number
        self._frame_timeout_ms = frame_timeout_ms
        self._startup_timeout_s = startup_timeout_s
        self._depth_processing = depth_processing
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._state = CameraState.STOPPED
        self._camera_id: str | None = None
        self._actual_profile: CameraProfile | None = None
        self._actual_alignment: AlignmentMode | None = None
        self._calibration: SensorCalibration | None = None
        self._latest_observation: AlignedRGBDObservation | None = None
        self._latest_color_frame: RGBFrame | None = None
        self._color_published_at = 0.0
        self._capture: LatestCapture | None = None
        self._unclosed_pipeline: Any | None = None
        self._latest_unfiltered_observation: AlignedRGBDObservation | None = None
        self._last_error: CameraError | None = None
        self._stream_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._frame_id = 0
        self._context: Any | None = None
        self._align_filter: Any | None = None
        self._point_cloud: Any | None = None
        self._depth_filter_chain: RealSenseDepthFilterChain | None = None
        self.set_observation_mode(observation_mode)

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
        with self._lock:
            return self._actual_profile

    @property
    def actual_alignment_mode(self) -> AlignmentMode | None:
        with self._lock:
            return self._actual_alignment

    @property
    def calibration(self) -> SensorCalibration | None:
        with self._lock:
            return self._calibration

    @property
    def depth_processing(self) -> DepthProcessingConfig:
        return self._depth_processing

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            camera_id=self.camera_id or self._serial_number or f"D435-{self._device_index}",
            supported_profiles=D435_SUPPORTED_PROFILES,
            software_alignment_profiles=D435_SUPPORTED_PROFILES,
            hardware_alignment_profiles=(),
        )

    def set_observation_mode(self, mode: str) -> None:
        if mode not in ("FINAL_ONLY", "RAW_AND_FILTERED"):
            raise ValueError("observation_mode 必须为 FINAL_ONLY 或 RAW_AND_FILTERED")
        with self._lock:
            if self._state != CameraState.STOPPED:
                raise CameraStateError("请在相机停止时设置观测模式")
            self._observation_mode = mode
            self._latest_unfiltered_observation = None

    def get_depth_filter_parameter_schemas(self) -> tuple[FilterParameterDescriptor, ...]:
        with self._lock:
            self._raise_if_failed()
            if self._depth_filter_chain is None:
                raise CameraStateError("相机尚未启动，无法读取滤波参数 schema")
            return self._depth_filter_chain.parameter_descriptors()

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._state != CameraState.STOPPED:
                    raise CameraStateError(f"当前状态 {self._state.value} 不允许启动；失败后请创建新实例")
                self._state = CameraState.STARTING
                self._latest_observation = self._latest_unfiltered_observation = None
                self._actual_profile = self._actual_alignment = self._calibration = None
                self._latest_color_frame = None
                self._last_error = None
                self._stop_event.clear()
                self._ready_event.clear()
                # frame_id 在同一实例的 stop/start 间也保持递增。
                self._stream_thread = threading.Thread(
                    target=self._stream_main, name="realsense-d435-camera", daemon=True,
                )
                self._stream_thread.start()
            startup_wait_s = self._startup_timeout_s + self._frame_timeout_ms / 1000.0 + _STARTUP_WAIT_GRACE_S
            if not self._ready_event.wait(startup_wait_s):
                error = CameraTimeoutError(f"D435 在 {self._startup_timeout_s:g} 秒内未发布首帧")
                self._fail(error)
                try:
                    self.stop()
                except CameraError:
                    pass  # 保留首帧超时作为根因；FAILED 禁止重启未清理的实例。
                raise error
            with self._lock:
                self._raise_if_failed()
                if self._state != CameraState.STREAMING or self._latest_observation is None:
                    raise CameraStateError("D435 在发布首帧前停止")

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._state == CameraState.CLOSED:
                    return
                self._stop_event.set()
                thread = self._stream_thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(self._frame_timeout_ms / 1000.0 + 2.0, _THREAD_STOP_TIMEOUT_S))
                if thread.is_alive():
                    error = CameraTimeoutError("D435 采集线程未按时退出，不能重启此实例")
                    self._fail(error)
                    raise error
            if self._unclosed_pipeline is not None:
                self._close_pipeline_session(self._unclosed_pipeline)
            with self._lock:
                if self._state != CameraState.FAILED:
                    self._state = CameraState.STOPPED

    def close(self) -> None:
        with self._lifecycle_lock:
            self.stop()
            with self._lock:
                self._state = CameraState.CLOSED

    def _raise_if_failed(self) -> None:
        if self._state == CameraState.FAILED and self._last_error is not None:
            raise self._last_error

    def _fail(self, error: CameraError) -> None:
        with self._lock:
            if self._state != CameraState.CLOSED:
                if self._state != CameraState.FAILED or self._last_error is None:
                    self._last_error = error
                self._state = CameraState.FAILED
                self._stop_event.set()
            self._ready_event.set()

    def get_latest_observation(self) -> AlignedRGBDObservation | None:
        with self._lock:
            self._raise_if_failed()
            return self._latest_observation

    def get_latest_color_frame(self) -> RGBFrame | None:
        with self._lock:
            if self._state == CameraState.FAILED and self._last_error is not None:
                raise self._last_error
            if self._stop_event.is_set() or time.monotonic() - self._color_published_at >= _RECOVERING_AFTER_S:
                return None
            return self._latest_color_frame

    def _publish_color_frame(self, frame: RGBFrame | None) -> None:
        with self._lock:
            if not self._stop_event.is_set():
                self._latest_color_frame = frame
                self._color_published_at = time.monotonic()

    def _stop_capture(self) -> None:
        if self._capture is not None:
            self._capture.close(self._frame_timeout_ms / 1000.0 + 2.0)
            self._capture = None
        with self._lock:
            self._latest_color_frame = None

    def get_latest_filter_comparison_observations(self) -> tuple[AlignedRGBDObservation, AlignedRGBDObservation] | None:
        with self._lock:
            self._raise_if_failed()
            if self._observation_mode != "RAW_AND_FILTERED":
                raise CameraStateError("请先设置 RAW_AND_FILTERED 模式")
            if self._latest_unfiltered_observation is None or self._latest_observation is None:
                return None
            return self._latest_unfiltered_observation, self._latest_observation

    def _stream_main(self) -> None:
        pipeline = None
        try:
            rs = self._load_sdk()
            pipeline, calibration = self._open_pipeline_session(rs)
            stable_result = self._wait_for_stable_observations(
                pipeline, calibration, required_count=1, timeout_s=self._startup_timeout_s,
            )
            if stable_result is None:
                return
            self._publish_streaming_observation(*stable_result, calibration)
            self._ready_event.set()

            while not self._stop_event.is_set():
                try:
                    self._consume_pipeline_session(pipeline, calibration)
                    break
                except CameraProfileError:
                    raise
                except Exception as error:
                    if self._stop_event.is_set():
                        break
                    self._enter_recovering(error)
                    self._close_pipeline_session(pipeline)
                    pipeline = None
                    result = self._rebuild_pipeline(rs, error)
                    if result is None:
                        break
                    pipeline, calibration = result
        except Exception as error:
            if not self._stop_event.is_set():
                self._fail(error if isinstance(error, CameraError) else CameraStreamError(str(error)))
        finally:
            if pipeline is not None:
                try:
                    self._close_pipeline_session(pipeline)
                except CameraTimeoutError:
                    _LOGGER.exception("Capture shutdown timed out; camera remains FAILED")
            with self._lock:
                if self._state not in (CameraState.FAILED, CameraState.CLOSED):
                    self._state = CameraState.STOPPED
                self._ready_event.set()

    def _open_pipeline_session(self, rs: Any) -> tuple[Any, SensorCalibration]:
        """创建全新 Pipeline/对齐/点云/滤波对象，失败时清理本轮资源。"""
        pipeline = None
        started = False
        try:
            device = self._select_device(rs)
            pipeline = rs.pipeline(self._context)
            config = self._build_config(rs, pipeline, device)
            if self._stop_event.is_set():
                raise CameraStateError("D435 已请求停止")
            active = pipeline.start(config)
            started = True
            self._validate_active_profile(active, rs)
            calibration = self._read_calibration(active, rs)
            self._align_filter = rs.align(rs.stream.color)
            self._point_cloud = rs.pointcloud()
            chain = RealSenseDepthFilterChain(rs, self._depth_processing)
            with self._lock:
                self._depth_filter_chain = chain
            self._capture = LatestCapture(
                lambda: self._read_capture_frames(pipeline), self._make_color_frame, self._publish_color_frame)
            self._capture.start()
            return pipeline, calibration
        except Exception:
            if started:
                self._close_pipeline_session(pipeline)
            else:
                self._clear_pipeline_session_references()
            raise

    def _close_pipeline_session(self, pipeline: Any) -> None:
        # Never destroy a pipeline while its reader still owns it.
        try:
            self._stop_capture()
        except CameraTimeoutError as error:
            self._unclosed_pipeline = pipeline
            with self._lock:
                self._last_error = error
                self._state = CameraState.FAILED
                self._stop_event.set()
                self._ready_event.set()
            raise
        self._unclosed_pipeline = None
        try:
            pipeline.stop()
        except Exception as error:
            # 断连后 stop 本身可能失败；与 G305 一样记录日志并继续释放/重建。
            _LOGGER.warning("停止 D435 Pipeline 时发生异常: %s", error)
        finally:
            self._clear_pipeline_session_references()

    def _clear_pipeline_session_references(self) -> None:
        with self._lock:
            self._depth_filter_chain = None
            self._align_filter = self._point_cloud = self._context = None

    def _consume_pipeline_session(self, pipeline: Any, calibration: SensorCalibration) -> None:
        """短时缺帧保留旧帧，5 秒进入恢复态，10 秒仍未稳定则重建。"""
        last_published_at = time.monotonic()
        consecutive_count = 0
        while not self._stop_event.is_set():
            frames = self._wait_complete_frames(pipeline)
            if self._stop_event.is_set():
                return
            now = time.monotonic()
            with self._lock:
                recovering = self._state == CameraState.RECOVERING
            if recovering and now - last_published_at >= _REBUILD_AFTER_S:
                raise CameraTimeoutError(f"连续 {_REBUILD_AFTER_S:g} 秒未恢复稳定 RGB-D 帧，准备重建 Pipeline")
            if frames is None:
                consecutive_count = 0
                if not recovering and now - last_published_at >= _RECOVERING_AFTER_S:
                    self._enter_recovering(CameraTimeoutError(f"连续 {_RECOVERING_AFTER_S:g} 秒未收到完整 RGB-D 帧"))
                continue
            raw, observation = self._make_observations(frames, calibration)
            if recovering:
                consecutive_count += 1
                if consecutive_count < _RECOVERY_STABLE_FRAME_COUNT:
                    continue
                self._publish_streaming_observation(raw, observation, calibration)
                consecutive_count = 0
                _LOGGER.info("D435 已连续收到 %d 帧，恢复 STREAMING", _RECOVERY_STABLE_FRAME_COUNT)
            else:
                self._publish_observation(raw, observation)
            last_published_at = time.monotonic()

    def _wait_for_stable_observations(
        self, pipeline: Any, calibration: SensorCalibration, required_count: int, timeout_s: float,
    ) -> tuple[AlignedRGBDObservation | None, AlignedRGBDObservation] | None:
        deadline = time.monotonic() + timeout_s
        consecutive_count = 0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            frames = self._wait_complete_frames(pipeline)
            if self._stop_event.is_set():
                return None
            if time.monotonic() >= deadline:
                break
            if frames is None:
                consecutive_count = 0
                continue
            observations = self._make_observations(frames, calibration)
            consecutive_count += 1
            if consecutive_count >= required_count:
                return observations
        if self._stop_event.is_set():
            return None
        raise CameraTimeoutError(f"在 {timeout_s:g} 秒内未连续收到 {required_count} 套完整 RGB-D 帧")

    def _rebuild_pipeline(self, rs: Any, initial_error: Exception) -> tuple[Any, SensorCalibration] | None:
        last_error = initial_error
        for attempt_index in range(_MAX_REBUILD_ATTEMPTS):
            if self._stop_event.wait(_REBUILD_BACKOFF_S[attempt_index]):
                return None
            pipeline = None
            try:
                _LOGGER.warning("正在重建 D435 Pipeline（第 %d/%d 次）", attempt_index + 1, _MAX_REBUILD_ATTEMPTS)
                pipeline, calibration = self._open_pipeline_session(rs)
                stable_result = self._wait_for_stable_observations(
                    pipeline, calibration, required_count=_RECOVERY_STABLE_FRAME_COUNT,
                    timeout_s=_REBUILD_FIRST_FRAME_TIMEOUT_S,
                )
                if stable_result is None:
                    self._close_pipeline_session(pipeline)
                    return None
                self._publish_streaming_observation(*stable_result, calibration)
                _LOGGER.info("D435 Pipeline 重建成功")
                return pipeline, calibration
            except CameraProfileError:
                if pipeline is not None:
                    self._close_pipeline_session(pipeline)
                raise
            except Exception as error:
                last_error = error
                if pipeline is not None:
                    self._close_pipeline_session(pipeline)
                _LOGGER.warning("D435 Pipeline 第 %d/%d 次重建失败: %s", attempt_index + 1, _MAX_REBUILD_ATTEMPTS, error)
        if self._stop_event.is_set():
            return None
        raise CameraStreamError(
            f"D435 Pipeline 连续 {_MAX_REBUILD_ATTEMPTS} 次重建失败；最后错误: {last_error}"
        ) from last_error

    def _enter_recovering(self, error: Exception) -> None:
        with self._lock:
            if self._stop_event.is_set() or self._state in (CameraState.CLOSED, CameraState.FAILED):
                return
            if self._state != CameraState.RECOVERING:
                _LOGGER.warning("D435 进入 RECOVERING: %s", error)
            self._state = CameraState.RECOVERING
            self._last_error = error if isinstance(error, CameraError) else CameraStreamError(str(error))
            self._latest_observation = self._latest_unfiltered_observation = None

    def _publish_streaming_observation(
        self, raw: AlignedRGBDObservation | None, observation: AlignedRGBDObservation,
        calibration: SensorCalibration,
    ) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._actual_profile = self._requested_profile
            self._actual_alignment = AlignmentMode.SOFTWARE
            self._calibration = calibration
            self._last_error = None
            self._state = CameraState.STREAMING
            self._publish_observation(raw, observation)

    def _publish_observation(self, raw: AlignedRGBDObservation | None, observation: AlignedRGBDObservation) -> None:
        with self._lock:
            if not self._stop_event.is_set():
                self._latest_observation = observation
                self._latest_unfiltered_observation = None if self._observation_mode == "FINAL_ONLY" else raw

    @staticmethod
    def _load_sdk() -> Any:
        try:
            import pyrealsense2 as rs
        except ImportError as error:
            raise CameraStreamError("无法导入 pyrealsense2；请在当前 Python 环境安装 RealSense SDK Python 包") from error
        return rs

    def _select_device(self, rs: Any) -> Any:
        self._context = rs.context()
        # 索引只在 D435 中计数，不会误选 D415、D455 或平台摄像头。
        devices = [device for device in self._context.query_devices()
                   if device.supports(rs.camera_info.name)
                   and device.get_info(rs.camera_info.name).split()[-1] == "D435"]
        expected_serial = self.camera_id or self._serial_number
        if expected_serial is not None:
            devices = [device for device in devices
                       if device.get_info(rs.camera_info.serial_number) == expected_serial]
            if not devices:
                raise CameraNotFoundError(f"未找到序列号 {expected_serial} 的 D435")
            device = devices[0]
        else:
            if self._device_index >= len(devices):
                raise CameraNotFoundError(f"请求 D435 索引 {self._device_index}，当前检测到 {len(devices)} 台")
            device = devices[self._device_index]
        serial = device.get_info(rs.camera_info.serial_number)
        if not serial:
            raise CameraStreamError("D435 未返回稳定序列号")
        with self._lock:
            self._camera_id = serial
        return device

    def _build_config(self, rs: Any, pipeline: Any, device: Any) -> Any:
        config = rs.config()
        config.enable_device(device.get_info(rs.camera_info.serial_number))
        p = self._requested_profile
        config.enable_stream(rs.stream.color, p.color_width, p.color_height, rs.format.rgb8, p.color_fps)
        config.enable_stream(rs.stream.depth, p.depth_width, p.depth_height, rs.format.z16, p.depth_fps)
        try:
            resolved = config.resolve(rs.pipeline_wrapper(pipeline))
            self._validate_active_profile(resolved, rs)
        except CameraProfileError:
            raise
        except Exception as error:
            if self.state == CameraState.RECOVERING:
                # resolve 也会因设备刚拔出/重新枚举而失败，不能误判为永久配置错误。
                # 已成功运行过的同一配置允许有限重试；明确的 Profile 不匹配仍立即终止。
                raise CameraStreamError("重连 D435 时 SDK 暂时无法解析原有流配置") from error
            raise CameraProfileError(f"D435 无法解析精确 Profile {p}；请检查设备、USB 3.x 连接及带宽") from error
        return config

    def _validate_active_profile(self, active: Any, rs: Any) -> None:
        p = self._requested_profile
        for stream, width, height, fps, fmt in (
            (rs.stream.color, p.color_width, p.color_height, p.color_fps, rs.format.rgb8),
            (rs.stream.depth, p.depth_width, p.depth_height, p.depth_fps, rs.format.z16),
        ):
            video = active.get_stream(stream).as_video_stream_profile()
            if (video.width(), video.height(), video.fps(), video.format()) != (width, height, fps, fmt):
                raise CameraProfileError(f"D435 实际 {stream} 流与请求的 {width}x{height}@{fps} {fmt} 不一致")
        if active.get_device().get_info(rs.camera_info.serial_number) != self.camera_id:
            raise CameraProfileError("D435 SDK 选择的设备与请求序列号不一致")

    def _read_capture_frames(self, pipeline: Any) -> Any | None:
        deadline = time.monotonic() + self._frame_timeout_ms / 1000.0
        while not self._stop_event.is_set():
            remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            received, frames = pipeline.try_wait_for_frames(remaining_ms)
            if not received:
                break
            if frames and frames.get_color_frame() and frames.get_depth_frame():
                return frames
        return None

    def _make_color_frame(self, frames: Any) -> RGBFrame:
        color = frames.get_color_frame()
        rgb = np.asanyarray(color.get_data())
        if rgb.dtype != np.uint8 or rgb.shape != (color.get_height(), color.get_width(), 3):
            raise CameraStreamError("D435 彩色帧不是预期 RGB8 数据")
        timestamp = color.get_timestamp()
        if not math.isfinite(timestamp) or timestamp < 0:
            raise CameraStreamError("D435 采集时间戳无效")
        with self._lock:
            self._frame_id += 1
            frame_id = self._frame_id
        return RGBFrame(frame_id, int(timestamp), rgb)

    def _wait_complete_frames(self, pipeline: Any) -> Any | None:
        packet = self._capture.get(self._frame_timeout_ms / 1000.0) if self._capture is not None else None
        frames = packet.frames if packet is not None else None
        if self._capture is None:
            frames = self._read_capture_frames(pipeline)
        if frames is None:
            return None
        if self._align_filter is None:
            raise CameraStreamError("D435 软件 D2C 尚未初始化")
        aligned = self._align_filter.process(frames).as_frameset()
        if aligned and aligned.get_color_frame() and aligned.get_depth_frame():
            return CapturedFrames(aligned, packet.color) if packet is not None else aligned
        return None

    @staticmethod
    def _read_calibration(active: Any, rs: Any) -> SensorCalibration:
        color = active.get_stream(rs.stream.color).as_video_stream_profile()
        depth = active.get_stream(rs.stream.depth).as_video_stream_profile()
        rgb_intr, depth_intr = color.get_intrinsics(), depth.get_intrinsics()

        def intrinsic(value: Any) -> CameraIntrinsics:
            return CameraIntrinsics(value.width, value.height, value.fx, value.fy, value.ppx, value.ppy)

        def distortion(value: Any) -> CameraDistortion:
            # 公共契约没有 model 字段；不能把非零逆向/鱼眼系数伪装成正向 Brown。
            # SDK 反投影明确禁止 modified Brown，零系数也不能绕过其断言。
            if value.model == rs.distortion.modified_brown_conrady:
                raise CameraProfileError("D435 点云反投影不支持 modified Brown 畸变模型")
            supported = (rs.distortion.none, rs.distortion.brown_conrady)
            if value.model not in supported and any(value.coeffs):
                raise CameraProfileError(f"公共 CameraDistortion 无法表达 D435 畸变模型 {value.model}")
            k1, k2, p1, p2, k3 = value.coeffs
            return CameraDistortion(k1, k2, k3, p1, p2)

        extrinsics = depth.get_extrinsics_to(color)
        return SensorCalibration(
            rgb_intrinsics=intrinsic(rgb_intr), depth_intrinsics=intrinsic(depth_intr),
            rgb_distortion=distortion(rgb_intr), depth_distortion=distortion(depth_intr),
            depth_to_rgb=RigidTransform(
                np.asarray(extrinsics.rotation, dtype=np.float64).reshape(3, 3, order="F"),
                np.asarray(extrinsics.translation, dtype=np.float64),
            ),
        )

    def _make_observations(self, frames: Any, calibration: SensorCalibration) -> tuple[AlignedRGBDObservation | None, AlignedRGBDObservation]:
        preview = frames.color if isinstance(frames, CapturedFrames) else self._make_color_frame(frames)
        if isinstance(frames, CapturedFrames):
            frames = frames.frames
        raw_depth = frames.get_depth_frame()
        rgb = preview.rgb
        filtered_depth = self._depth_filter_chain.process(raw_depth) if self._depth_filter_chain else raw_depth
        depth_m = self._depth_frame_to_m(filtered_depth)
        if depth_m.shape != rgb.shape[:2]:
            raise CameraStreamError("D435 D2C 后深度尺寸与 RGB 不一致")
        cloud = self._build_organized_point_cloud(filtered_depth, depth_m, calibration)
        # rs.align 保留原深度相机 Z；公共深度与点云统一为彩色光学系 Z。
        depth_m = np.where(np.isfinite(cloud[..., 2]), cloud[..., 2], 0.).astype(np.float32)
        common = dict(frame_id=preview.frame_id, capture_timestamp_ms=preview.capture_timestamp_ms, rgb=rgb,
                      profile=self._requested_profile, alignment_mode=AlignmentMode.SOFTWARE, calibration=calibration)
        final = AlignedRGBDObservation(**common, depth_m=depth_m, point_cloud_m=cloud,
                                       depth_processing=self._depth_processing)
        raw = None
        if self._observation_mode == "RAW_AND_FILTERED":
            raw_m = self._depth_frame_to_m(raw_depth)
            raw_cloud = self._build_organized_point_cloud(raw_depth, raw_m, calibration)
            raw_m = np.where(np.isfinite(raw_cloud[..., 2]), raw_cloud[..., 2], 0.).astype(np.float32)
            raw = AlignedRGBDObservation(**common, depth_m=raw_m,
                                         point_cloud_m=raw_cloud,
                                         depth_processing=DepthProcessingConfig())
        return raw, final

    @staticmethod
    def _depth_frame_to_m(depth_frame: Any) -> np.ndarray:
        raw = np.asanyarray(depth_frame.get_data())
        if raw.dtype != np.uint16 or raw.shape != (depth_frame.get_height(), depth_frame.get_width()):
            raise CameraStreamError("D435 深度帧不是预期 Z16 数据")
        units = depth_frame.get_units()  # meters per raw unit，不能再除以 1000。
        if not math.isfinite(units) or units <= 0:
            raise CameraStreamError("D435 深度单位无效")
        return raw.astype(np.float32) * np.float32(units)

    def _build_organized_point_cloud(self, depth_frame: Any, depth_m: np.ndarray, calibration: SensorCalibration) -> np.ndarray:
        if self._point_cloud is None:
            raise CameraStreamError("D435 点云处理器尚未初始化")
        # 输入为 D2C 后的深度；SDK 使用该帧的内参/畸变反投影，不能照搬
        # 忽略畸变的针孔公式。复制 vertices，避免 SDK 重用内部存储。
        points = self._point_cloud.calculate(depth_frame)
        cloud = np.asanyarray(points.get_vertices()).view(np.float32).reshape(*depth_m.shape, 3).copy()
        invalid = ~np.isfinite(depth_m) | (depth_m <= 0) | ~np.isfinite(cloud).all(axis=2) | (cloud[..., 2] <= 0)
        # align.cpp 将原始 Z_d 搬到 RGB 像素；SDK 点云暂为 ray_rgb * Z_d。
        # 对 P_rgb = R P_depth + t，有 Z_d = R[:,2] dot (P_rgb - t)。
        # 沿 RGB 射线修正尺度，恢复真正的 RGB 光学系 XYZ；保留 SDK D2C 的
        # 像素栅格化精度，不把 Z_d 直接误称为 Z_rgb。
        depth_axis_in_rgb = calibration.depth_to_rgb.rotation[:, 2].astype(np.float32)
        offset = np.float32(np.dot(depth_axis_in_rgb, calibration.depth_to_rgb.translation_m))
        denominator = np.sum(cloud * depth_axis_in_rgb, axis=2)
        scale = np.zeros_like(depth_m)
        np.divide(depth_m + offset, denominator, out=scale,
                  where=~invalid & (np.abs(denominator) > 1e-8))
        invalid |= ~np.isfinite(scale) | (scale <= 0)
        cloud *= scale[..., None]
        cloud[invalid] = np.nan
        return cloud
