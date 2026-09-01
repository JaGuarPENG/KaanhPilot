"""常驻的单帧拍照选目标与抓取应用。

H：回 Home；P：冻结一帧相机画面；1/2/3：选择目标并重新拍照抓取；
Esc：取消选择；Q：退出。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
import queue
import threading
import time
from typing import Any

import cv2
import numpy as np
import roboticstoolbox as rtb

from commands.robot_commands import RobotCommandExecutor
from commands.setup import RobotSetup
from commands.single_shot import SingleShotExecutor
from perception.roi_localizer import RoiPointCloudLocalizer
from robot.kaanh_backend import KaanhRobotBackend
from robot.robot_dh import create_ka_ur
from yolo.contracts.yolo_structs import Detection


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"
WINDOW_NAME = "snapshot_pick"


class SnapshotPickState(Enum):
    IDLE = auto()
    HOMING = auto()
    CAPTURING = auto()
    SELECTING = auto()
    EXECUTING = auto()


@dataclass(frozen=True, slots=True)
class SnapshotCandidate:
    key: str
    target_id: str
    detection: Detection
    target_point_camera_m: tuple[float, float, float] | None


class SnapshotPickApp:
    """管理一套常驻机器人、相机和 YOLO 资源的交互应用。"""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self._setup = RobotSetup(config_path)
        self._config = self._setup.get_robot_config()

        # 资源只在 App 生命周期中创建一次；start() 中才真正连接/启动设备。
        self._monitor_robot = KaanhRobotBackend(
            self._config.robot.ip, 5888, self._config.robot.udp_port, timeout=3
        )
        self._control_robot = KaanhRobotBackend(
            self._config.robot.ip,
            self._config.robot.control_port,
            self._config.robot.udp_port,
            timeout=60,
        )
        self._camera = self._setup.setup_camera(0)
        self._detector = self._setup.setup_detector()
        self._localizer = RoiPointCloudLocalizer(self._config.localization)
        self._camera_transform = self._setup.setup_camera_transform()
        self._single_shot = SingleShotExecutor(
            robot=self._control_robot,
            camera=self._camera,
            detector=self._detector,
            localizer=self._localizer,
            camera_transform=self._camera_transform,
            tracker_config=self._config.tracker,
        )
        self._target_ids = self._detector.target_ids[:3]

        self._state = SnapshotPickState.IDLE
        self._joints_deg: list[float] | None = None
        self._candidates: dict[str, SnapshotCandidate] = {}
        self._preview_image: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._robot_commands: queue.Queue[tuple[str, Any | None]] = queue.Queue()
        self._vision_commands: queue.Queue[str] = queue.Queue()
        self._started = False
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """建立设备连接、启动相机、预热模型，并在成功后启动工作线程。"""
        if self._started:
            raise RuntimeError("SnapshotPickApp 已启动")
        if not self._target_ids:
            raise RuntimeError("模型标签配置中没有可选择目标")

        try:
            self._connect_control_robot()
            self._connect_monitor_robot()
            self._camera.start()
            time.sleep(self._config.camera_warmup_seconds)
            first_frame = self._camera.get_latest_observation()
            self._detector.warmup(first_frame.rgb.shape[1], first_frame.rgb.shape[0])
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

            self._stop_event.clear()
            self._threads = [
                threading.Thread(target=self._monitor_loop, name="snapshot-monitor", daemon=True),
                threading.Thread(target=self._control_loop, name="snapshot-control", daemon=True),
                threading.Thread(target=self._vision_loop, name="snapshot-vision", daemon=True),
            ]
            for thread in self._threads:
                thread.start()
            self._started = True
            print("[初始化] 机器人、相机和 YOLO 已就绪。")
        except Exception:
            self.shutdown()
            raise

    def run(self) -> None:
        """启动应用并运行 3D 机器人窗口主循环。"""
        self.start()
        robot_model = create_ka_ur()
        environment = rtb.backends.PyPlot.PyPlot()
        environment.launch()
        environment.add(robot_model)
        environment.ax.set_xlim([-0.8, 0.8])
        environment.ax.set_ylim([-0.8, 0.8])
        environment.ax.set_zlim([0.0, 1.2])
        environment.ax.figure.canvas.mpl_connect("key_press_event", self._on_key_press)
        print("\nH: Home | P: 单帧拍照 | 1/2/3: 选择并抓取 | Esc: 取消 | Q: 退出")

        try:
            while not self._stop_event.is_set():
                with self._lock:
                    joints_deg = self._joints_deg
                if joints_deg is not None:
                    robot_model.q = np.deg2rad(np.asarray(joints_deg, dtype=float))
                self._poll_snapshot_window()
                environment.step(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """停止线程并关闭本 App 拥有的设备资源；可安全重复调用。"""
        if self._stop_event.is_set() and not self._started:
            return
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        self._camera.close()
        self._monitor_robot.close()
        self._control_robot.close()
        try:
            cv2.destroyWindow(WINDOW_NAME)
        except cv2.error:
            pass
        self._started = False
        print("[退出] 资源已关闭。")

    # ---------- 状态机入口 ----------

    def _on_key_press(self, event: Any) -> None:
        self._handle_key(event.key or "")

    def _handle_key(self, key: str) -> None:
        key = key.lower()
        if key == "h":
            self._request_home()
        elif key == "p":
            self._request_capture()
        elif key in {"1", "2", "3"}:
            self._select_target(key)
        elif key == "escape":
            self._cancel_selection()
        elif key == "q":
            self._stop_event.set()

    def _request_home(self) -> None:
        with self._lock:
            if self._state is not SnapshotPickState.IDLE:
                print(f"[忽略] 当前状态为 {self._state.name}，不能回 Home。")
                return
            self._state = SnapshotPickState.HOMING
        self._robot_commands.put(("move_home", None))
        print("[Home] 已提交。")

    def _request_capture(self) -> None:
        with self._lock:
            if self._state not in (SnapshotPickState.IDLE, SnapshotPickState.SELECTING):
                print(f"[忽略] 当前状态为 {self._state.name}，不能拍照。")
                return
            self._candidates.clear()
            self._preview_image = None
            self._state = SnapshotPickState.CAPTURING
        self._vision_commands.put("capture_once")
        print("[相机] 正在抓取当前画面…")

    def _select_target(self, key: str) -> None:
        with self._lock:
            if self._state is not SnapshotPickState.SELECTING:
                print("[提示] 请先按 P 抓取画面。")
                return
            candidate = self._candidates.get(key)
            if candidate is None:
                print(f"[选择] 当前画面中没有目标 {key}；请按 P 重拍。")
                return
            self._state = SnapshotPickState.EXECUTING
        # execute() 会再次取最新相机帧、重新定位后才发起真实运动。
        self._robot_commands.put(("execute_pick", candidate.target_id))
        print(f"[选择] 已选择 [{key}] {candidate.target_id}，正在重新拍照并计算抓取点。")

    def _cancel_selection(self) -> None:
        with self._lock:
            if self._state is not SnapshotPickState.SELECTING:
                return
            self._candidates.clear()
            self._preview_image = None
            self._state = SnapshotPickState.IDLE
        print("[选择] 已取消。")

    def _set_idle(self) -> None:
        with self._lock:
            self._state = SnapshotPickState.IDLE

    # ---------- 长期运行线程 ----------

    def _connect_control_robot(self) -> None:
        if not self._control_robot.connect():
            raise RuntimeError("无法连接机器人控制端口")
        self._control_robot.login(self._config.robot.user, self._config.robot.password)
        self._control_robot.manual_enable()
        self._control_robot.set_pgm_vel(20)
        self._control_robot.set_jog_vel(30)
        self._control_robot.set_jog_coordinate()
        self._control_robot.set_tool(tool_id=0)

    def _connect_monitor_robot(self) -> None:
        if not self._monitor_robot.connect():
            raise RuntimeError("无法连接机器人监控端口")
        self._monitor_robot.login(self._config.robot.user, self._config.robot.password)

    def _monitor_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                data = self._monitor_robot.get_status()
                motion = ((data or {}).get("ret_context") or {}).get("motion_msg") or {}
                motor_pos = motion.get("motor_pos")
                if isinstance(motor_pos, list) and motor_pos and isinstance(motor_pos[0], list):
                    with self._lock:
                        self._joints_deg = motor_pos[0]
                time.sleep(0.02)
        except Exception as error:
            print(f"[监控] 异常: {error}")

    def _control_loop(self) -> None:
        executor = RobotCommandExecutor(self._control_robot)
        while not self._stop_event.is_set():
            try:
                command, payload = self._robot_commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if command == "move_home":
                    executor.move_init_pose()
                    print("[Home] 完成。")
                elif command == "execute_pick":
                    self._single_shot.execute(str(payload))
                    print(f"[抓取] {payload} 的预抓取运动完成。")
                else:
                    print(f"[控制] 未知命令: {command}")
            except Exception as error:
                print(f"[控制] {command} 失败: {error}")
            finally:
                self._set_idle()
                self._robot_commands.task_done()

    def _vision_loop(self) -> None:
        """只处理 P 的预览任务；相机、模型在 start() 中已完成一次初始化。"""
        while not self._stop_event.is_set():
            try:
                command = self._vision_commands.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if command != "capture_once":
                    continue
                observation = self._camera.get_latest_observation()
                candidates: dict[str, SnapshotCandidate] = {}
                for index, target_id in enumerate(self._target_ids, start=1):
                    detections = self._detector.detect(observation, target_id).detections
                    if not detections:
                        continue
                    detection = max(detections, key=lambda item: item.confidence)
                    localization = self._localizer.localize(observation, detection)
                    key = str(index)
                    candidates[key] = SnapshotCandidate(
                        key=key,
                        target_id=target_id,
                        detection=detection,
                        target_point_camera_m=localization.target_point_camera_m,
                    )
                with self._lock:
                    self._candidates = candidates
                    self._preview_image = self._draw_snapshot(observation, candidates)
                    self._state = SnapshotPickState.SELECTING
                if candidates:
                    print("[相机] 画面已冻结：" + "，".join(
                        f"{key}={candidate.target_id}" for key, candidate in candidates.items()
                    ))
                else:
                    print("[相机] 未检测到目标；P 重拍或 Esc 取消。")
            except Exception as error:
                print(f"[相机] 抓拍失败: {error}")
                self._set_idle()
            finally:
                self._vision_commands.task_done()

    # ---------- GUI ----------

    @staticmethod
    def _draw_snapshot(
        observation: Any, candidates: dict[str, SnapshotCandidate]
    ) -> np.ndarray:
        image = np.ascontiguousarray(observation.rgb[..., ::-1].copy())
        colors = {"1": (0, 220, 0), "2": (0, 210, 255), "3": (255, 120, 0)}
        for key, candidate in candidates.items():
            x1, y1, x2, y2 = candidate.detection.bbox_xyxy
            color = colors[key]
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                image,
                f"[{key}] {candidate.target_id} {candidate.detection.confidence:.2f}",
                (x1, max(45, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                2,
            )
        cv2.putText(
            image,
            "1/2/3 select | P recapture | Esc cancel | Q quit",
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
        )
        return image

    def _poll_snapshot_window(self) -> None:
        try:
            with self._lock:
                image = self._preview_image
            if image is not None:
                cv2.imshow(WINDOW_NAME, image)
            code = cv2.waitKey(1) & 0xFF
        except cv2.error:
            return
        if code == 255:
            return
        if code == 27:
            self._handle_key("escape")
        elif code < 128:
            self._handle_key(chr(code))


def main() -> None:
    SnapshotPickApp().run()


if __name__ == "__main__":
    main()
