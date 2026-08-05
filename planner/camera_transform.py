import numpy as np

from dataclasses import dataclass, replace

from camera.contracts.cam_structs import AlignedRGBDObservation
from perception.percept_structs import TargetPerceptionResult, TargetStatus
from planner.pose import quaternion_to_rotation


@dataclass(frozen=True, slots=True)
class RobotCameraExtrinsic:
    """机器人相机标定外参
    
    参数表：
    - cam_0_extrinsic: 眼在手外头部相机外参
    - cam_1_extrinsic: 眼在手上右手相机外参
    - cam_2_extrinsic: 眼在手上左手相机外参
    """
    cam_0_extrinsic: dict
    cam_1_extrinsic: dict
    cam_2_extrinsic: dict

@dataclass(frozen=True, slots=True)
class TransformResult:
    """坐标变换变换结果
    
    参数表：
    - target_id: 目标id
    - frame_id: 相机观测帧id
    - capture_timestamp_ms: 相机观测时间戳
    - status: 目标状态
    - target_point_base_m: 目标点在机器人基座坐标系下的三维坐标，单位为米；如果无法定位则为 None。
    """
    target_id: str
    frame_id: int
    capture_timestamp_ms: int
    status: TargetStatus
    target_point_base_m: tuple[float, float, float] | None

    def __post_init__(self) -> None:
        if not self.target_id:
            raise ValueError("target_id 不能为空")
        if self.frame_id < 1 or self.capture_timestamp_ms < 0:
            raise ValueError("frame_id 或 capture_timestamp_ms 非法")
        if not isinstance(self.status, TargetStatus):
            raise TypeError("status 必须是 TargetStatus")
        if self.target_point_base_m is None:
            if self.status in (
                TargetStatus.TARGET_ACQUIRED,
                TargetStatus.TARGET_TRACKED,
            ):
                raise ValueError("已获取或跟踪目标时必须提供基座坐标点")
            return
        point = np.asarray(self.target_point_base_m, dtype=float)
        if point.shape != (3,) or not np.isfinite(point).all():
            raise ValueError("target_point_base_m 必须是有限的 3D 坐标")
        if self.status not in (
            TargetStatus.TARGET_ACQUIRED,
            TargetStatus.TARGET_TRACKED,
        ):
            raise ValueError("无有效目标状态不能携带基座坐标点")
        object.__setattr__(
            self,
            "target_point_base_m",
            tuple(float(value) for value in point),
        )

class CameraTransform:
    def __init__(self, cam_extrinsic: RobotCameraExtrinsic):
        self.cam_extrinsic = cam_extrinsic

    def _load_camera_extrinsic(self, cam_extrinsic: RobotCameraExtrinsic):
        """加载相机外参"""
        self.cam_extrinsic = cam_extrinsic

    def get_camera_extrinsic(self, cam_index: int) -> dict:
        """获取指定相机的外参"""
        if cam_index == 0:
            return self.cam_extrinsic.cam_0_extrinsic
        elif cam_index == 1:
            return self.cam_extrinsic.cam_1_extrinsic
        elif cam_index == 2:
            return self.cam_extrinsic.cam_2_extrinsic
        else:
            raise ValueError("无效的相机索引，必须为 0、1 或 2")
        
    
    def observation2base(self, observation: AlignedRGBDObservation, cam_index: int, rbt_pq: list[float]|None) -> AlignedRGBDObservation:
        """将 RGBD 观测对齐到机器人坐标系"""
        # 这里可以实现对齐逻辑，例如使用相机内参和外参进行坐标变换
        # 具体实现取决于相机模型和观测数据的格式
        if cam_index not in [0, 1, 2]:
            raise ValueError("无效的相机索引，必须为 0、1 或 2")
        if cam_index == 0:
            #眼在手外实现
            extrinsic = self.get_camera_extrinsic(0)
            translation = extrinsic["translation_m"]
            rotation_pq = extrinsic["quaternion_xyzw"]
            rotation_rm = quaternion_to_rotation(rotation_pq)
            # 提取点云
            point_cloud_m = observation.point_cloud_m
            # 将点云从相机坐标系转换到机器人坐标系
            point_cloud_m_transformed = (rotation_rm @ point_cloud_m.reshape(-1, 3).T).T + np.array(translation)
            point_cloud_m_transformed = point_cloud_m_transformed.reshape(point_cloud_m.shape)
        else:
            #眼在手上
            if rbt_pq is None or len(rbt_pq) != 7:
                raise ValueError("机器人位姿 rbt_pq 必须为长度为 7 的列表，包含位置和四元数")
            extrinsic = self.get_camera_extrinsic(cam_index)
            translation = extrinsic["translation_m"]
            rotation_pq = extrinsic["quaternion_xyzw"]
            rotation_rm = quaternion_to_rotation(rotation_pq)
            # 提取点云
            point_cloud_m = observation.point_cloud_m
            # 将点云从相机坐标系转换到末端执行器坐标系
            point_cloud_m_transformed = (rotation_rm @ point_cloud_m.reshape(-1, 3).T).T + np.array(translation)
            # 将点云从末端执行器坐标系转换到机器人基座坐标系
            rbt_translation_m = np.asarray(rbt_pq[:3], dtype=float) / 1000.0
            rbt_rotation_pq = rbt_pq[3:7]
            rbt_rotation_rm = quaternion_to_rotation(rbt_rotation_pq)
            point_cloud_m_transformed = (rbt_rotation_rm @ point_cloud_m_transformed.reshape(-1, 3).T).T + np.array(rbt_translation_m)
            point_cloud_m_transformed = point_cloud_m_transformed.reshape(point_cloud_m.shape)
        # 创建新的观测对象，包含变换后的点云
        base_observation = replace(
            observation,
            point_cloud_m=point_cloud_m_transformed.astype(np.float32),
        )
        return base_observation

    def result2base(self, result: TargetPerceptionResult, cam_index: int, rbt_pq: list[float]|None) -> TransformResult:
        """将检测结果对齐到机器人坐标系"""
        # 这里可以实现对齐逻辑，例如使用相机内参和外参进行坐标变换
        # 校验
        if cam_index not in [0, 1, 2]:
            raise ValueError("无效的相机索引，必须为 0、1 或 2")
        localization = result.localization
        has_point = (
            localization is not None
            and localization.target_point_camera_m is not None
        )
        if result.status in (
            TargetStatus.NO_MATCH,
            TargetStatus.NO_TARGET_POINT,
            TargetStatus.TARGET_LOST):
            if has_point:
                raise ValueError(f"当前状态为:{result.status}，目标状态不应携带相机坐标点")
            else:
                return TransformResult(
                    target_id=result.target_id,
                    frame_id=result.frame_id,
                    capture_timestamp_ms=result.capture_timestamp_ms,
                    status=result.status,
                    target_point_base_m=None,
                )
        target_point_cam_m = localization.target_point_camera_m
        if target_point_cam_m is None:
            raise ValueError("无法获取目标点在相机坐标系下的三维坐标")
        if cam_index == 0:
            #眼在手外
            extrinsic = self.get_camera_extrinsic(0)
            translation = extrinsic["translation_m"]
            rotation_pq = extrinsic["quaternion_xyzw"]
            rotation_rm = quaternion_to_rotation(rotation_pq)
            target_point_base_m = (rotation_rm @ np.asarray(target_point_cam_m, dtype=float)) + np.array(translation)
            target_point_base_m = tuple(float(value) for value in target_point_base_m)
        else:
            #眼在手上
            if rbt_pq is None or len(rbt_pq) != 7:
                raise ValueError("机器人位姿 rbt_pq 必须为长度为 7 的列表，包含位置和四元数")
            extrinsic = self.get_camera_extrinsic(cam_index)
            translation = extrinsic["translation_m"]
            rotation_pq = extrinsic["quaternion_xyzw"]
            rotation_rm = quaternion_to_rotation(rotation_pq)
            target_point_end_m = (rotation_rm @ np.asarray(target_point_cam_m, dtype=float)) + np.array(translation)
            # 将点云从末端执行器坐标系转换到机器人基座坐标系
            rbt_translation_m = np.asarray(rbt_pq[:3], dtype=float) / 1000.0
            rbt_rotation_pq = rbt_pq[3:7]
            rbt_rotation_rm = quaternion_to_rotation(rbt_rotation_pq)
            target_point_base_m = (rbt_rotation_rm @ target_point_end_m) + np.array(rbt_translation_m)
            target_point_base_m = tuple(float(value) for value in target_point_base_m)
        return TransformResult(
            target_id=result.target_id,
            frame_id=result.frame_id,
            capture_timestamp_ms=result.capture_timestamp_ms,
            status=result.status,
            target_point_base_m=target_point_base_m,
        )
