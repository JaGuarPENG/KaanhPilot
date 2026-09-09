"""可供其他程序直接调用的单目标拍照抓取指令。

本模块从 ``snapshot_pick.py`` 中提取完成一次抓取真正需要的行为：

1. 接收调用方已经初始化完成的机器人，并初始化相机和视觉资源；
2. 根据调用方给出的目标 ID 重新拍照、定位并移动到抓取位置；
3. 控制灵巧手完成张开、抓取、释放和机械臂后退动作；
4. 在使用结束后关闭本类拥有的相机资源。

窗口、键盘事件、机器人可视化和后台监控线程仍属于交互应用层，因此不放入
这个类中。这样其他 Python 程序不需要启动 GUI，也可以直接执行抓取。
"""

from __future__ import annotations

import math
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from robot.kaanh_backend import KaanhRobotBackend


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"


class SnapshotPickCommand:
    """管理抓取所需的视觉资源，并使用外部机器人执行完整抓取流程。

    构造对象时只保存参数，不连接硬件。调用方必须先调用
    :meth:`initialize_resources`，随后可以多次调用 :meth:`pick`，最后调用
    :meth:`close` 释放资源。

    也可以使用上下文管理器，自动完成初始化和资源释放：

    .. code-block:: python

        with SnapshotPickCommand(robot) as picker:
            picker.pick("oolong_tea")

    参数：
        robot：由调用方创建并完成连接、登录、使能的机器人实例。机器人
            生命周期归调用方所有，本类不会连接、初始化或关闭该实例。
        config_path：项目配置目录。
        arm_model_id：执行抓取的机械臂模型编号；当前支持 0（臂 1）和
            1（臂 2）。注意：目前 ``SingleShotExecutor`` 的抓取运动固定
            使用臂 1，因此现阶段该参数必须为 0。参数保留在这里，是为了
            明确资源和后退动作属于哪条手臂，并方便后续扩展双臂抓取。
        hand_id：控制器中的灵巧手从站 ID。
        pregrasp_offset_m：工具坐标系下的预抓取三维偏置 ``(x, y, z)``，
            单位为米。
        retreat_offset_mm：抓取结束后，工具坐标系下的机械臂后退三维偏置，
            单位为毫米。
        hold_seconds：闭合灵巧手后保持抓取的时间，单位为秒。
    """

    def __init__(
        self,
        robot: KaanhRobotBackend,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        *,
        arm_model_id: int = 0,
        hand_id: int = 15,
        pregrasp_offset_m: Sequence[float] = (0.0, -0.04, 0.285),
        retreat_offset_mm: Sequence[float] = (-50.0, 0.0, -100.0),
        hold_seconds: float = 5.0,
    ) -> None:
        if robot is None:
            raise ValueError("robot 不能为空，必须传入已初始化的机器人实例")
        if isinstance(arm_model_id, bool) or arm_model_id not in (0, 1):
            raise ValueError("arm_model_id 只能是 0（臂1）或 1（臂2）")
        if arm_model_id != 0:
            raise NotImplementedError(
                "当前 SingleShotExecutor 的抓取运动仅支持臂1（arm_model_id=0）"
            )
        if isinstance(hand_id, bool) or not isinstance(hand_id, int) or hand_id < 0:
            raise ValueError("hand_id 必须是非负整数")

        self._config_path = Path(config_path)
        self._arm_model_id = arm_model_id
        self._hand_id = hand_id
        self._pregrasp_offset_m = self._normalize_vector3(
            pregrasp_offset_m,
            name="pregrasp_offset_m",
        )
        self._retreat_offset_mm = self._normalize_vector3(
            retreat_offset_mm,
            name="retreat_offset_mm",
        )
        self._hold_seconds = float(hold_seconds)
        if not math.isfinite(self._hold_seconds) or self._hold_seconds < 0.0:
            raise ValueError("hold_seconds 必须是大于等于 0 的有限数值")

        # 机器人由调用方创建和初始化，本类只保存引用，不拥有其生命周期。
        # initialize_resources() 不会连接、登录、使能或关闭该机器人。
        self._robot = robot

        # 以下视觉相关属性均由 initialize_resources() 创建。构造函数不会
        # 启动相机或加载检测模型，调用方可自行选择合适的初始化时机。
        self._setup = None
        self._config = None
        self._camera = None
        self._detector = None
        self._localizer = None
        self._camera_transform = None
        self._single_shot = None
        self._robot_executor = None

        self._initialized = False
        # 抓取、初始化和关闭都涉及同一套硬件，使用可重入锁将这些操作串行化，
        # 防止多个调用线程同时向机器人或相机发出命令。
        self._operation_lock = threading.RLock()

    @property
    def is_initialized(self) -> bool:
        """资源是否已经初始化完成并可执行抓取。"""
        return self._initialized

    @property
    def available_target_ids(self) -> tuple[str, ...]:
        """返回当前检测模型支持的目标 ID；初始化前返回空元组。"""
        if self._detector is None:
            return ()
        return tuple(self._detector.target_ids)

    def initialize_resources(self) -> None:
        """创建并预热完成抓取所需的相机和视觉资源。

        初始化内容包括：

        - 读取项目配置；
        - 创建并启动 RGB-D 相机；
        - 创建目标检测器、点云定位器和相机外参变换器；
        - 创建单帧抓取执行器和通用机器人指令执行器；
        - 获取第一帧相机观测并预热目标检测模型。

        本方法可以安全地重复调用；已经成功初始化时会直接返回。如果中途
        失败，会关闭已经创建的相机资源，然后把原始异常继续抛给调用方。

        注意：传入的机器人必须已经由调用方完成连接、登录、模式设置和使能；
        本方法不会改变机器人的连接状态或运行模式。
        """
        with self._operation_lock:
            if self._initialized:
                return

            # 把项目内依赖延迟到真正初始化时导入，使其他程序仅导入本模块时
            # 不会立即加载相机驱动、YOLO 或机器人动态库。
            from commands.robot_commands import RobotCommandExecutor
            from commands.setup import RobotSetup
            from commands.single_shot import SingleShotExecutor

            try:
                self._setup = RobotSetup(self._config_path)
                self._config = self._setup.get_robot_config()

                self._camera = self._setup.setup_camera(0)
                self._detector = self._setup.setup_detector()
                self._localizer = self._setup.setup_localizer()
                self._camera_transform = self._setup.setup_camera_transform()

                self._single_shot = SingleShotExecutor(
                    robot=self._robot,
                    camera=self._camera,
                    detector=self._detector,
                    localizer=self._localizer,
                    camera_transform=self._camera_transform,
                    tracker_config=self._config.tracker,
                    offset_m=self._pregrasp_offset_m,
                )
                self._robot_executor = RobotCommandExecutor(self._robot)

                # 相机启动后等待自动曝光和深度流稳定，再用真实画面预热模型，
                # 避免第一次调用 pick() 时额外承担模型加载和 CUDA 初始化延迟。
                self._camera.start()
                time.sleep(self._config.camera_warmup_seconds)
                first_observation = self._camera.get_latest_observation()
                if first_observation is None:
                    raise RuntimeError("相机启动后未能获取用于模型预热的观测帧")
                height, width = first_observation.rgb.shape[:2]
                self._detector.warmup(width, height)

                if not self._detector.target_ids:
                    raise RuntimeError("检测模型没有配置任何可抓取目标")

                self._initialized = True
                print(
                    "[SnapshotPickCommand] 相机、检测器和定位资源已就绪。"
                )
            except Exception:
                # 初始化可能在任意视觉资源步骤失败。统一清理可以避免残留
                # 相机流或半初始化对象影响下一次重试。外部机器人不受影响。
                self._close_resources_unlocked()
                raise

    def pick(self, target_id: str) -> None:
        """给定目标 ID，执行一次完整的拍照抓取流程。

        流程与 ``snapshot_pick.py`` 当前的 ``execute_pick`` 行为一致：

        1. 使能并张开灵巧手；
        2. 获取最新相机帧，检测并定位指定目标；
        3. 移动到预抓取位置，再沿末端方向移动到抓取位置；
        4. 闭合灵巧手并保持 ``hold_seconds``；
        5. 张开灵巧手释放目标；
        6. 指定机械臂沿当前末端坐标系执行后退偏置。

        资源未初始化、目标 ID 无效、视觉定位失败或机器人运动失败时，本方法
        会抛出异常，由调用程序决定重试、报警或停止设备。
        """
        with self._operation_lock:
            self._require_initialized()
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError("target_id 必须是非空字符串")
            if target_id not in self._detector.target_ids:
                available = ", ".join(self._detector.target_ids)
                raise ValueError(
                    f"检测模型不支持目标 {target_id!r}；可用目标: {available}"
                )

            print(f"[SnapshotPickCommand] 开始抓取目标: {target_id}")

            # 每次任务开始时重新使能灵巧手并张开，保证抓取动作具有确定起点。
            self._robot.hand_en(self._hand_id)
            self._open_hand()

            # SingleShotExecutor 内部完成最新帧采集、目标检测、点云定位、
            # 相机坐标到机器人基坐标的转换、预抓取运动和最终接近运动。
            self._single_shot.execute(target_id)
            print(f"[SnapshotPickCommand] {target_id} 已到达抓取位置。")

            # 闭合灵巧手夹持目标，并按配置保留一段时间。
            self._close_hand()
            # print(f"[SnapshotPickCommand] {target_id} 抓取完成。")
            # if self._hold_seconds > 0.0:
            #     time.sleep(self._hold_seconds)

            # # 保持 snapshot_pick.py 的现有行为：等待结束后张开手释放目标，
            # # 然后沿当前工具坐标系退回，避免直接按基坐标轴移动。
            # self._open_hand()
            # print(f"[SnapshotPickCommand] {target_id} 已释放。")
            self._robot_executor.move_arm_by_tool_offset(
                self._arm_model_id,
                self._retreat_offset_mm,
            )
            self._robot_executor.move_transport_pose()
            print(f"[SnapshotPickCommand] {target_id} 抓取流程完成，机械臂已后退。")

    def close(self) -> None:
        """关闭本类拥有的相机和视觉资源；不会关闭外部机器人。"""
        with self._operation_lock:
            self._close_resources_unlocked()

    def __enter__(self) -> SnapshotPickCommand:
        """进入 ``with`` 代码块时自动初始化全部资源。"""
        self.initialize_resources()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """退出 ``with`` 代码块时始终释放硬件资源。"""
        self.close()

    def _open_hand(self) -> None:
        """使用 snapshot_pick.py 中的张手参数复位灵巧手。"""
        self._robot.hand_move(
            id=self._hand_id,
            j1=6000,
            j2=0,
            j3=0,
            j4=0,
            j5=0,
            j6=0,
            vel=1000,
            cur=1000,
        )

    def _close_hand(self) -> None:
        """使用 snapshot_pick.py 中的闭合参数夹持目标。"""
        self._robot.hand_move(
            id=self._hand_id,
            j1=6000,
            j2=5800,
            j3=6000,
            j4=6000,
            j5=6000,
            j6=6000,
            vel=1000,
            cur=1000,
        )

    def _require_initialized(self) -> None:
        """确保所有执行抓取所需的资源已经准备完成。"""
        if (
            not self._initialized
            or self._robot is None
            or self._camera is None
            or self._detector is None
            or self._single_shot is None
            or self._robot_executor is None
        ):
            raise RuntimeError(
                "资源尚未初始化，请先调用 initialize_resources()"
            )

    def _close_resources_unlocked(self) -> None:
        """在已持有操作锁时关闭资源，并清空所有运行期对象。"""
        # 相机可能只完成了部分初始化，因此关闭操作必须允许重复调用。
        # 机器人由调用方传入，本类在任何情况下都不会关闭它。
        close_errors: list[Exception] = []
        if self._camera is not None:
            try:
                self._camera.close()
            except Exception as error:
                close_errors.append(error)
        self._initialized = False
        self._robot_executor = None
        self._single_shot = None
        self._camera_transform = None
        self._localizer = None
        self._detector = None
        self._camera = None
        self._config = None
        self._setup = None

        if close_errors:
            print(
                "[SnapshotPickCommand] 资源关闭过程中发生异常: "
                + "; ".join(str(error) for error in close_errors)
            )

    @staticmethod
    def _normalize_vector3(values: Sequence[float], *, name: str) -> tuple[float, float, float]:
        """校验并规范化三维偏置，避免错误数据被发送给真实机器人。"""
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{name} 必须是包含 x、y、z 的三维数值序列")
        try:
            normalized = tuple(float(value) for value in values)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                f"{name} 必须是包含 x、y、z 的三维数值序列"
            ) from error
        if len(normalized) != 3:
            raise ValueError(f"{name} 必须包含 x、y、z 三个数值")
        if not all(math.isfinite(value) for value in normalized):
            raise ValueError(f"{name} 不能包含 NaN 或无穷大")
        return normalized
