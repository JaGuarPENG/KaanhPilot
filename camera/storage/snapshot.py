"""将厂商无关的 RGB-D 观测以可复现形式保存到本地。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from camera.contracts.models import AlignedRGBDObservation


@dataclass(frozen=True, slots=True)
class SavedSnapshot:
    """一次原子快照写入的结果。"""

    timestamp_name: str
    image_path: Path
    cloud_npz_path: Path
    cloud_ply_path: Path


def save_observation_snapshot(observation: AlignedRGBDObservation, camera_id: str | None, root_dir: Path) -> SavedSnapshot:
    """保存 RGB PNG 与过滤后观测的无损压缩 NPZ。

    文件名由主机本地系统时间生成；NPZ 内同时保留设备采集时间戳，二者不可混淆。
    使用临时文件后替换，避免中途失败留下名称相同的半成品文件。
    """
    timestamp_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    picture_dir = root_dir / "save" / "pic"
    cloud_dir = root_dir / "save" / "cloud"
    picture_dir.mkdir(parents=True, exist_ok=True)
    cloud_dir.mkdir(parents=True, exist_ok=True)

    image_path = picture_dir / f"{timestamp_name}.png"
    cloud_npz_path = cloud_dir / f"{timestamp_name}.npz"
    cloud_ply_path = cloud_dir / f"{timestamp_name}.ply"
    temporary_image = picture_dir / f".{timestamp_name}.tmp.png"
    temporary_npz = cloud_dir / f".{timestamp_name}.tmp.npz"
    temporary_ply = cloud_dir / f".{timestamp_name}.tmp.ply"
    metadata = {
        "camera_id": camera_id,
        "frame_id": observation.frame_id,
        "capture_timestamp_ms": observation.capture_timestamp_ms,
        "profile": asdict(observation.profile),
        "alignment_mode": observation.alignment_mode.value,
        "depth_processing": asdict(observation.depth_processing),
    }
    try:
        # 公共观测使用 RGB；OpenCV 写入磁盘时需要 BGR 通道顺序。
        if not cv2.imwrite(str(temporary_image), cv2.cvtColor(observation.rgb, cv2.COLOR_RGB2BGR)):
            raise RuntimeError(f"无法写入图片文件：{temporary_image}")
        np.savez_compressed(
            temporary_npz,
            point_cloud_m=observation.point_cloud_m,
            depth_m=observation.depth_m,
            rgb=observation.rgb,
            metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)),
        )
        _write_colored_point_cloud_ply(temporary_ply, observation.point_cloud_m, observation.rgb)
        temporary_image.replace(image_path)
        temporary_npz.replace(cloud_npz_path)
        temporary_ply.replace(cloud_ply_path)
    except Exception:
        # 删除仅由当前调用创建的临时文件，已完成的正式文件不会被覆盖。
        temporary_image.unlink(missing_ok=True)
        temporary_npz.unlink(missing_ok=True)
        temporary_ply.unlink(missing_ok=True)
        raise
    return SavedSnapshot(timestamp_name, image_path, cloud_npz_path, cloud_ply_path)


def _write_colored_point_cloud_ply(path: Path, point_cloud_m: np.ndarray, rgb: np.ndarray) -> None:
    """写入 CloudCompare 可直接读取的二进制小端彩色 PLY。

    PLY 是去除 NaN 后的非组织化点云，坐标仍为 camera_optical_frame；完整
    H x W 组织结构、无效点和元数据保留在同名 NPZ 中。
    """
    points = point_cloud_m.reshape(-1, 3)
    colors = rgb.reshape(-1, 3)
    valid = np.isfinite(points).all(axis=1)
    points, colors = points[valid], colors[valid]
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    vertices = np.empty(
        len(points),
        dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")],
    )
    vertices["x"], vertices["y"], vertices["z"] = points[:, 0], points[:, 1], points[:, 2]
    vertices["red"], vertices["green"], vertices["blue"] = colors[:, 0], colors[:, 1], colors[:, 2]
    with path.open("wb") as stream:
        stream.write(header.encode("ascii"))
        vertices.tofile(stream)


if __name__ == "__main__":
    print("该模块需要 AlignedRGBDObservation；请运行 python -m commands.camera_snapshot_command。")
