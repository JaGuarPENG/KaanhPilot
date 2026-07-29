# Gemini 305 相机模块（Phase 1）

本目录提供独立的 RGB-D 相机能力，不修改也不依赖项目现有的 `controller`、`robot`、`commands` 或 `planner` 运行逻辑。

## 运行环境

相机 SDK 已安装在 Conda 环境 `pyagent` 中。请从项目根目录运行：

```powershell
conda run -n pyagent python -m camera.demo --profile 1280 --alignment auto --seconds 5
```

另一种明确 Profile：

```powershell
conda run -n pyagent python -m camera.demo --profile 848 --alignment software --seconds 5
```

`--alignment` 支持 `hardware`、`software` 与 `auto`。`auto` 优先硬件 D2C，只有在当前 Profile 无法使用硬件 D2C 时才切换为软件 D2C。

## 数据约定

- `rgb`：`uint8 H x W x 3`，RGB 通道顺序。
- `depth_m`：`float32 H x W`，单位为米，且已注册到 RGB 像素坐标系。
- `point_cloud_m`：`float32 H x W x 3`，单位为米，坐标系为 `camera_optical_frame`；无效深度为 `NaN`。
- 每帧带模块生成的递增 `frame_id` 与 SDK 设备采集时间戳 `capture_timestamp_ms`。

所有观测数组在发布后均为只读。消费者不能接触 `pyorbbecsdk`、`Pipeline` 或厂商帧对象。

## 目录

- `contracts/`：厂商无关的数据类、接口和异常类型。消费者只依赖这一层。
- `adapters/orbbec/g305.py`：唯一导入奥比中光 SDK 的 G305 适配器。
- `adapters/orbbec/profiles.py`：G305 的明确 Profile 声明。
- `visualization/rgbd_viewer.py`：只消费公共观测的 RGB-D 与 Open3D 点云可视化工具。
- `demo.py`：不接入机器人控制器的真实硬件验证入口。

## 十秒可视化命令

新增命令在 `commands/camera_commands.py` 中，不修改现有 controller。默认使用 848x480@60、自动 D2C，并持续十秒：

```powershell
conda run -n pyagent python -m commands.camera_commands
```

可显式选择 Profile 与对齐方式：

```powershell
conda run -n pyagent python -m commands.camera_commands --profile 1280 --alignment software --seconds 10
```

该命令打开 RGB-D 图像窗口与可交互的彩色点云窗口。图像窗口按 `Q`、`ESC` 或关闭窗口将提前结束任务；`--no-point-cloud` 可只验证 RGB-D 图像显示。

点云窗口仅为方便观察，将 `camera_optical_frame` 的 X 右、Y 下、Z 前坐标旋转为 Open3D 的 X 右、Y 上、Z 朝观察者坐标。`AlignedRGBDObservation.point_cloud_m` 的原始光学坐标不变，不能把此显示变换用于定位或机器人坐标转换。

## 官方深度滤波

默认不启用任何滤波。需要时，G305 适配器按奥比中光官方样例的固定顺序执行：时域滤波、空间滤波、破洞修补、深度阈值。滤波发生在 D2C 后，保证输出深度和点云仍与 RGB 像素对齐。

```powershell
python -m commands.camera_commands --profile 848 --alignment auto --seconds 10 `
  --temporal-filter --spatial-filter --hole-filling `
  --spatial-magnitude 1 --spatial-alpha 0.5 --hole-filling-mode 0 `
  --minimum-depth-m 0.15 --maximum-depth-m 2.0
```

阈值必须同时给出 `--minimum-depth-m` 和 `--maximum-depth-m`，单位为米；适配器会转换为官方 SDK 所需的毫米。处理配置会记录在每个 `AlignedRGBDObservation.depth_processing` 中。

当前开放的滤波参数：`--minimum-depth-m`、`--maximum-depth-m`、`--spatial-magnitude`（1-5）、`--spatial-alpha`（0.1-1.0）和 `--hole-filling-mode`（0=TOP，1=NEAREST，2=FAREST）。时域参数、`disp_diff` 与 `radius` 保持 SDK 默认值。

在决定哪些默认参数值得开放前，可先读取当前设备和 SDK 实际提供的参数 schema：

```powershell
python -m commands.camera_filter_schema_command --profile 848 --alignment auto
```

命令会输出 JSON 数组，其中包含每个参数的滤波器名称、参数名、说明、类型、默认值、最小值、最大值和步长。请保留完整输出用于后续参数选择。

## 滤波前后点云对比

以下命令仅打开两个 Open3D 点云窗口，左侧为同帧未过滤点云，右侧为应用官方滤波链后的点云；默认持续 60 秒：

```powershell
python -m commands.camera_filter_comparison_command --profile 848 --alignment auto
```

默认启用时域、空间、破洞修补和 `0.15m-2.0m` 阈值。`--no-temporal-filter`、`--no-spatial-filter`、`--no-hole-filling` 可逐项关闭；`--minimum-depth-m` 与 `--maximum-depth-m` 可调整阈值范围。关闭任一 Open3D 窗口会结束对比任务。

比较命令同样支持 `--spatial-magnitude`、`--spatial-alpha` 与 `--hole-filling-mode`，用于观察这些参数对点云的即时影响。

## 单次快照

以下命令打开相机、取得一帧过滤后观测并自动关闭相机：

```powershell
python -m commands.camera_snapshot_command --profile 848 --alignment auto `
  --spatial-filter --hole-filling `
  --spatial-magnitude 2 --spatial-alpha 0.6 --hole-filling-mode 1 `
  --minimum-depth-m 0.15 --maximum-depth-m 2.0
```

快照默认预热 2 秒，给自动曝光与时域滤波稳定的时间；可用 `--warmup-seconds 3` 调整。图片保存为项目根目录 `save/pic/<系统时间>.png`。

过滤后点云会保存两种格式，均位于项目根目录 `save/cloud/`：

- `<同一系统时间>.npz`：完整 `H x W x 3` 有组织点云、深度、RGB 和元数据，适合代码读取。
- `<同一系统时间>.ply`：去除无效点后的彩色二进制 PLY，可直接在 CloudCompare 打开。

NPZ 的 `metadata_json` 记录相机 ID、设备采集时间、Profile、对齐模式和此次滤波配置。

在 Windows 上出现 SDK 错误日志时，`conda run` 可能因控制台编码而无法完整转发输出。此时请先激活环境再直接运行：

```powershell
conda activate pyagent
python -m commands.camera_commands --profile 848 --alignment auto --seconds 10
```
