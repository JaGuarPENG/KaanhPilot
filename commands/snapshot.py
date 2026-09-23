"""单帧目标点观测。

本模块只负责从一帧新的 RGB-D 观测中识别并定位指定目标，不发送任何
机器人运动指令。显示与保存行为由构造参数控制，调用者无需使用命令行参数。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from perception.roi_localizer import RoiPointCloudLocalizer
    from perception.target_tracker import TrackerConfig
    from planner.camera_transform import CameraTransform
    from robot.kaanh_backend import KaanhRobotBackend
    from yolo.detector import Detector


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAVE_ROOT = PROJECT_ROOT / "save" / "target_point"


class TargetPointUnavailableError(RuntimeError):
    """当前单帧中没有可供机器人使用的有效目标点。"""


@dataclass(frozen=True, slots=True)
class TargetPoint:
    """一次成功目标观测的输出。

    ``target_point_camera_m`` 位于相机光学坐标系，``target_point_base_m``
    位于机器人基坐标系；两者单位均为米。该对象描述物体上的三维目标点，
    不是可以直接发送给机器人的 TCP 位姿。
    """

    target_id: str
    frame_id: int
    capture_timestamp_ms: int
    target_point_camera_m: tuple[float, float, float]
    target_point_base_m: tuple[float, float, float]
    detection_confidence: float
    valid_point_count: int

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target_id 不能为空")
        if self.frame_id < 1 or self.capture_timestamp_ms < 0:
            raise ValueError("帧编号或采集时间戳非法")
        for name, value in (
            ("target_point_camera_m", self.target_point_camera_m),
            ("target_point_base_m", self.target_point_base_m),
        ):
            point = tuple(float(item) for item in value)
            if len(point) != 3 or not all(math.isfinite(item) for item in point):
                raise ValueError(f"{name} 必须是三个有限数值")
            object.__setattr__(self, name, point)
        if not 0.0 <= self.detection_confidence <= 1.0:
            raise ValueError("detection_confidence 必须位于 [0, 1]")
        if self.valid_point_count < 1:
            raise ValueError("valid_point_count 必须为正数")


class SnapShotCommand:
    """拍摄一个新帧并输出指定目标的 :class:`TargetPoint`。

    参数均在构造对象时传入：

    - ``robot``：已连接、登录并使能的机器人；本类不拥有也不会关闭它。
    - ``camera``：已启动并能持续提供观测的相机；本类不启动也不关闭它。
    - ``detector``：外部创建的目标检测器；本类只负责预热和调用。
    - ``localizer``：外部创建的 ROI 点云定位器。
    - ``camera_transform``：外部创建的相机到机器人基座坐标变换器。
    - ``tracker_config``：创建本次单目标跟踪器所需的配置。
    - ``show_yolo_result``：是否显示 YOLO 框、定位 ROI 和目标点信息。
    - ``show_point_cloud_result``：是否显示用于计算目标点的最终 ROI 点云。
    - ``is_save``：是否保存 RGB、叠加图、点云数据和 JSON 结果。

    显示开关打开时，``capture_once`` 会保持调试窗口，直至用户按 Q、Esc
    或关闭窗口；生产调用应关闭两个显示开关。外部相机必须由调用方提前启动，
    本类只在 ``initialize_resources`` 中检查外部资源并预热检测器。可使用
    不同 ``target_id`` 连续调用多次 ``capture_once``，每次都等待一个大于上一帧
    编号的新观测。所有传入依赖的生命周期都归调用方管理，本类不会关闭
    相机，也不会销毁检测器、定位器或坐标变换器。
    """

    def __init__(
        self,
        robot: KaanhRobotBackend,
        camera: Any,
        detector: Detector,
        localizer: RoiPointCloudLocalizer,
        camera_transform: CameraTransform,
        tracker_config: TrackerConfig,
        *,
        camera_extrinsic_index: int = 1,
        robot_model_id: int = 0,
        show_yolo_result: bool = False,
        show_point_cloud_result: bool = False,
        is_save: bool = False,
        save_root: Path | str = DEFAULT_SAVE_ROOT,
        fresh_frame_timeout_s: float = 2.0,
    ) -> None:
        if robot is None:
            raise ValueError("robot 不能为空")
        if camera is None:
            raise ValueError("camera 不能为空，必须传入已启动的相机实例")
        if detector is None:
            raise ValueError("detector 不能为空，必须传入外部创建的检测器")
        if localizer is None:
            raise ValueError("localizer 不能为空，必须传入外部创建的定位器")
        if camera_transform is None:
            raise ValueError("camera_transform 不能为空，必须传入外部创建的坐标变换器")
        if tracker_config is None:
            raise ValueError("tracker_config 不能为空，必须传入跟踪器配置")
        if isinstance(camera_extrinsic_index, bool) or camera_extrinsic_index not in (0, 1, 2):
            raise ValueError("camera_extrinsic_index 只能是 0、1 或 2")
        if isinstance(robot_model_id, bool) or robot_model_id not in (0, 1):
            raise ValueError("robot_model_id 只能是 0（臂1）或 1（臂2）")
        try:
            normalized_timeout_s = float(fresh_frame_timeout_s)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("fresh_frame_timeout_s 必须为正数") from error
        if not math.isfinite(normalized_timeout_s) or normalized_timeout_s <= 0:
            raise ValueError("fresh_frame_timeout_s 必须为正数")

        self._robot = robot
        self._camera = camera
        self._detector = detector
        self._localizer = localizer
        self._camera_transform = camera_transform
        self._tracker_config = tracker_config
        self._camera_extrinsic_index = int(camera_extrinsic_index)
        self._robot_model_id = int(robot_model_id)
        self._show_yolo_result = bool(show_yolo_result)
        self._show_point_cloud_result = bool(show_point_cloud_result)
        self._is_save = bool(is_save)
        self._save_root = Path(save_root)
        self._fresh_frame_timeout_s = normalized_timeout_s

        self._last_frame_id = 0
        self._last_save_directory: Path | None = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def last_save_directory(self) -> Path | None:
        """最近一次保存结果的目录；未保存时为 ``None``。"""
        return self._last_save_directory

    def initialize_resources(self) -> None:
        """检查外部感知资源并预热 YOLO；不会启动或创建任何依赖。"""
        if self._initialized:
            return

        try:
            first_observation = self._camera.get_latest_observation()
            if first_observation is None:
                raise RuntimeError("外部相机没有观测帧，请先启动并预热相机")
            height, width = first_observation.rgb.shape[:2]
            self._detector.warmup(width, height)
            self._last_frame_id = int(first_observation.frame_id)
            self._initialized = True
        except Exception:
            self.close()
            raise

    def capture_once(self, target_id: str) -> TargetPoint | None:
        """等待一个新帧，执行一次 YOLO 推理并返回目标点。

        本方法不会控制机器人运动。机器人必须在拍照时保持静止，否则眼在手上
        相机的帧和 TCP 位姿不能形成可靠的坐标变换。YOLO 未选中指定目标时
        返回 ``None``；已经检测到目标但无法取得有效三维点时仍抛出异常。
        """
        self._require_initialized()
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("target_id 必须是非空字符串")
        target_id = target_id.strip()
        if target_id not in self._detector.target_ids:
            available = ", ".join(self._detector.target_ids)
            raise ValueError(
                f"检测模型不支持目标 {target_id!r}；可用目标: {available}"
            )
        observation = self._wait_for_fresh_observation()
        robot_tcp_pq = self._read_stable_robot_tcp_pq()

        from perception.session import TargetPerceptionSession
        from perception.target_tracker import SingleTargetTracker

        session = TargetPerceptionSession(
            detector=self._detector,
            localizer=self._localizer,
            tracker=SingleTargetTracker(self._tracker_config),
            target_id=target_id,
        )
        # TargetPerceptionSession.process 内只调用一次 detector.detect。
        result = session.process(observation)
        localization = result.localization
        if result.detection is None:
            self._publish_debug_result(
                observation,
                result,
                None,
                robot_tcp_pq,
            )
            return None
        if localization is None or localization.target_point_camera_m is None:
            self._publish_debug_result(
                observation,
                result,
                None,
                robot_tcp_pq,
            )
            raise TargetPointUnavailableError(
                f"目标 {target_id!r} 没有有效三维点，状态={result.status.value}"
            )

        transform_result = self._camera_transform.result2base(
            result=result,
            cam_index=self._camera_extrinsic_index,
            rbt_pq=robot_tcp_pq,
        )
        if transform_result.target_point_base_m is None:
            raise TargetPointUnavailableError(
                f"目标 {target_id!r} 无法转换到机器人基坐标系"
            )

        target_point = TargetPoint(
            target_id=target_id,
            frame_id=result.frame_id,
            capture_timestamp_ms=result.capture_timestamp_ms,
            target_point_camera_m=tuple(localization.target_point_camera_m),
            target_point_base_m=tuple(transform_result.target_point_base_m),
            detection_confidence=float(result.detection.confidence),
            valid_point_count=int(localization.valid_point_count),
        )

        self._publish_debug_result(
            observation,
            result,
            target_point,
            robot_tcp_pq,
        )
        return target_point

    def close(self) -> None:
        """结束当前使用状态；不会关闭或销毁任何外部依赖。"""
        self._initialized = False
        self._last_frame_id = 0

    def __enter__(self) -> SnapShotCommand:
        self.initialize_resources()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _require_initialized(self) -> None:
        if (
            not self._initialized
            or self._camera is None
            or self._detector is None
            or self._localizer is None
            or self._camera_transform is None
        ):
            raise RuntimeError("资源尚未初始化，请先调用 initialize_resources()")

    def _wait_for_fresh_observation(self) -> Any:
        deadline = time.monotonic() + self._fresh_frame_timeout_s
        while time.monotonic() < deadline:
            observation = self._camera.get_latest_observation()
            if observation is not None and observation.frame_id > self._last_frame_id:
                self._last_frame_id = int(observation.frame_id)
                return observation
            time.sleep(0.005)
        raise TimeoutError(
            f"{self._fresh_frame_timeout_s:.2f}s 内没有收到新的相机观测帧"
        )

    def _read_stable_robot_tcp_pq(self) -> list[float]:
        state = self._robot.get_robot_state()
        if state is None:
            raise RuntimeError("无法读取机器人状态")
        if bool(getattr(state, "has_error", False)):
            raise RuntimeError(
                f"机器人存在错误，错误码={getattr(state, 'error_code', None)}"
            )
        if bool(getattr(state, "moving", False)):
            raise RuntimeError("机器人仍在运动，不能进行眼在手上单帧定位")

        tcp_pq = None
        get_model = getattr(state, "get_model", None)
        if callable(get_model):
            model_state = get_model(self._robot_model_id)
            if model_state is not None:
                tcp_pq = getattr(model_state, "tcp_pq", None)
        try:
            normalized = [float(value) for value in tcp_pq]
        except (TypeError, ValueError, OverflowError) as error:
            raise RuntimeError("无法获取所选机械臂的有效 TCP PQ") from error
        if len(normalized) != 7 or not all(math.isfinite(value) for value in normalized):
            raise RuntimeError("机器人 TCP PQ 必须包含七个有限数值")
        return normalized

    def _publish_debug_result(
        self,
        observation: Any,
        result: Any,
        target_point: TargetPoint | None,
        robot_tcp_pq: list[float],
    ) -> None:
        """按构造参数保存或显示本次结果，包括未检测到目标的有效空结果。"""
        self._last_save_directory = None
        if self._is_save:
            self._last_save_directory = self._save_result(
                observation,
                result,
                target_point,
                robot_tcp_pq,
            )
        if self._show_yolo_result or self._show_point_cloud_result:
            self._show_result(observation, result)

    def _save_result(
        self,
        observation: Any,
        result: Any,
        target_point: TargetPoint | None,
        robot_tcp_pq: list[float],
    ) -> Path:
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("保存单帧调试结果需要安装 opencv-python") from error

        from visualization.rgb_overlay import draw_target_perception

        timestamp_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        save_directory = self._save_root / timestamp_name
        save_directory.mkdir(parents=True, exist_ok=False)

        raw_bgr = np.ascontiguousarray(observation.rgb[..., ::-1])
        overlay_bgr = raw_bgr.copy()
        draw_target_perception(cv2, overlay_bgr, result)
        if not cv2.imwrite(str(save_directory / "rgb.png"), raw_bgr):
            raise RuntimeError("无法保存 RGB 图像")
        if not cv2.imwrite(str(save_directory / "yolo_result.png"), overlay_bgr):
            raise RuntimeError("无法保存 YOLO 叠加图")

        inspection = (
            None if result.localization is None else result.localization.inspection
        )
        points = (
            np.empty((0, 3), dtype=np.float32)
            if inspection is None
            else inspection.points_m
        )
        colors = (
            np.empty((0, 3), dtype=np.uint8)
            if inspection is None
            else inspection.colors_rgb
        )
        np.savez_compressed(
            save_directory / "observation_and_target_cloud.npz",
            rgb=observation.rgb,
            depth_m=observation.depth_m,
            point_cloud_m=observation.point_cloud_m,
            target_cloud_points_m=points,
            target_cloud_colors_rgb=colors,
            target_point_camera_m=np.asarray(
                () if target_point is None else target_point.target_point_camera_m
            ),
            target_point_base_m=np.asarray(
                () if target_point is None else target_point.target_point_base_m
            ),
        )

        payload: dict[str, Any] = {
            "status": result.status.value,
            "target_point": None if target_point is None else asdict(target_point),
        }
        if result.detection is not None:
            payload["detection"] = {
                "confidence": float(result.detection.confidence),
                "bbox_xyxy": result.detection.bbox_xyxy,
            }
        if result.localization is not None:
            payload["localization"] = {
                "valid_point_count": int(result.localization.valid_point_count),
                "depth_median_m": result.localization.depth_median_m,
                "depth_spread_m": result.localization.depth_spread_m,
            }
        payload["robot_tcp_pq"] = robot_tcp_pq
        payload["camera_extrinsic_index"] = self._camera_extrinsic_index
        payload["robot_model_id"] = self._robot_model_id
        (save_directory / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return save_directory

    def _show_result(self, observation: Any, result: Any) -> None:
        cv2 = None
        window_name = "Single Shot YOLO Result"
        overlay_bgr = None
        point_cloud_viewer = None
        try:
            if self._show_yolo_result:
                try:
                    import cv2 as cv2_module
                except ImportError as error:
                    raise RuntimeError("显示 YOLO 结果需要安装 opencv-python") from error
                from visualization.rgb_overlay import draw_target_perception

                cv2 = cv2_module
                overlay_bgr = np.ascontiguousarray(observation.rgb[..., ::-1].copy())
                draw_target_perception(cv2, overlay_bgr, result)
                cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

            if self._show_point_cloud_result:
                from visualization.viewer_3d import Viewer3D

                point_cloud_viewer = Viewer3D()

            while True:
                if cv2 is not None:
                    cv2.imshow(window_name, overlay_bgr)
                    key = cv2.waitKey(20) & 0xFF
                    if key in (27, ord("q")):
                        break
                    if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                        break
                else:
                    time.sleep(0.02)

                if point_cloud_viewer is not None:
                    if not point_cloud_viewer.update(result):
                        break
                elif cv2 is None:
                    break
        finally:
            if point_cloud_viewer is not None:
                point_cloud_viewer.close()
            if cv2 is not None:
                try:
                    cv2.destroyWindow(window_name)
                except cv2.error:
                    pass
