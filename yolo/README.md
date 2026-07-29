# YOLO 单目标感知

本模块支持当前 `.pt` 模型中的三个目标：`mineral_water`、`oolong_tea`、`coco_cola`。

`yolo/contracts/` 将 Ultralytics 的原始输出转换为稳定的 `Detection`；`perception/` 不依赖任何 YOLO 或相机 SDK 类型，只接收统一的相机观测和检测结果，完成单目标跨帧关联与 ROI 点云定位。

## 环境

相机运行环境需要 `ultralytics`、`PyYAML`、OpenCV 以及已有的奥比中光 SDK：

```powershell
conda activate pyagent
pip install ultralytics PyYAML
```

当前标签映射位于 `yolo/model/yolov8_0728.labels.yaml`。未来替换或扩展模型时，修改该文件左侧的模型类别名和右侧的稳定目标标识；加载时会校验它们是否存在于 `.pt` 内，避免 class_id 顺序变化导致错配。

## 实时命令

以下命令只追踪 `mineral_water` 的一个实例。首次取最高置信度候选；目标锁定后，仅接受满足 IoU 或中心像素距离约束的候选框，不会因为另一个同类实例置信度更高而切换。

```powershell
conda activate pyagent
python -m commands.yolo_camera_command `
  --target mineral_water `
  --profile 848 --alignment auto `
  --minimum-depth-m 0.15 --maximum-depth-m 2.0 `
  --static-roi 100 80 750 450 `
  --workspace-min -0.50 -0.40 0.15 `
  --workspace-max 0.50 0.40 2.00
```

加上 `--show-point-cloud` 会额外打开 Open3D 窗口：仅显示已经完成全部 ROI 过滤和深度内点筛选的点云，红色球体是同一批点计算出的抓取点。显示线程只保留最新一帧，不能反向阻塞识别和定位。JSON 的 `timing_ms` 包含 YOLO、追踪、ROI 定位与处理总耗时；`process_window_ms` 给出最近 100 帧的均值、P50、P95、P99 和最大值。

`--static-roi`、`--workspace-min` 和 `--workspace-max` 均为可选用户过滤范围。未提供时只去除无效点；提供深度范围时最小值和最大值必须成对出现。窗口运行在独立线程，按 `Q`、`Esc` 或关闭窗口只关闭显示，不停止后端感知。用 `Ctrl+C` 终止整个实时任务。

## 单帧命令

```powershell
conda activate pyagent
python -m commands.yolo_snapshot_command `
  --target coco_cola `
  --minimum-depth-m 0.15 --maximum-depth-m 2.0
```

## 保存点云离线检查

G305 独占相机流时，不应启动第二个实时相机进程做显示。以下命令不连接相机，只加载已有快照 NPZ，复用相同的 YOLO、ROI 过滤和抓取点算法：

```powershell
python -m commands.yolo_saved_visualization_command `
  --image save/pic/20260729_104730_128875.png `
  --cloud-npz save/cloud/20260729_104730_128875.npz `
  --target mineral_water `
  --minimum-depth-m 0.15 --maximum-depth-m 2.0
```

PNG 是 YOLO 唯一输入，产生 bbox 和 ROI；NPZ 仅提供同名帧的有组织点云。命令会拒绝文件名或像素尺寸不一致的组合，防止 ROI 与点云错帧。加 `--no-display` 可用于只验证 JSON 结果和阶段耗时。现有快照没有完整相机标定，故离线命令不能用于坐标变换；当前 ROI 定位不依赖该标定，因此显示与抓取点计算不受影响。

两个命令都输出 JSON。三维 `target_point_camera_m` 使用米为单位，坐标系是 `camera_optical_frame`；本模块不进行手眼标定或机器人基座坐标变换。
