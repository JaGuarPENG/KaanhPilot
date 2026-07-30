"""项目的本地诊断可视化能力。

本包只读取相机观测和感知结果，不控制设备、不修改点云，也不参与机器人决策。
为避免仅使用坐标转换时加载 OpenCV/Open3D，查看器需要从具体子模块显式导入，
例如 ``from visualization.camera_viewer import ObservationVisualizer``。
"""

__all__: list[str] = []
