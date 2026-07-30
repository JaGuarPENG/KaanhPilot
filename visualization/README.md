# Visualization Module

`visualization/` 是本地诊断和人工检查的展示层。它只读取相机观测、检测结果和感知结果，不拥有 G305 设备、不修改点云、不做目标选择，也不向机器人发送命令。

目录职责：

- `coordinates.py`：仅用于 Open3D 显示的坐标翻转，不能用于定位或机器人坐标变换。
- `rgb_overlay.py`：在 RGB 图像副本上绘制检测框、最终 ROI、抓取点和耗时。
- `camera_viewer.py`：RGB-D 与完整有组织点云诊断窗口。
- `filter_comparison_viewer.py`：相机深度过滤前后的点云对比窗口。
- `perception_viewer.py`：最终 ROI 内点与抓取点窗口。

窗口由 `commands/` 创建和关闭。实时 YOLO 命令将显示放在独立线程和单帧队列中；点云窗口刷新慢或被关闭不会阻塞检测、追踪、ROI 过滤或 JSON 输出。

旧的 `camera.visualization.*` 和 `perception.visualization` 路径保留为兼容导出，新代码应直接导入顶层 `visualization`。
