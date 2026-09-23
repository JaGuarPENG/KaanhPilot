# RealSense D435 适配器

与 `camera/adapters/orbbec/` 一样，分为 `d435.py`（后台相机流）、
`profiles.py`（明确的 RGB-D 流组合）和 `filters.py`（官方 SDK 滤波链）。
输出统一的 `AlignedRGBDObservation`，SDK 对象不会流入感知或规划模块。

## 项目使用的 Profile

仅保留当前项目经实机确认、彩色流和深度流均支持的两组配置：

| 常量 | 彩色（RGB8） | 深度（Z16） |
| --- | --- | --- |
| `D435_640X480_30` | 640×480@30 | 640×480@30 |
| `D435_1280X720_6` | 1280×720@6 | 1280×720@6 |

SDK 将传感器原生彩色格式转换为 RGB8。
启动前后都会核对精确流配置，不支持时抛出 `CameraProfileError`。

## 独立使用

在运行项目的 Python 环境安装 `pyrealsense2`，例如激活 `pyagent` 后执行
`python -m pip install pyrealsense2`。请按本机 Python/系统支持情况选择并验证版本。
导入适配器和 Profile 不要求 SDK 已安装；`start()` 才加载 SDK。

```python
from camera.adapters.realsense.d435 import RealSenseD435Camera
from camera.adapters.realsense.profiles import D435_640X480_30
from camera.contracts.cam_structs import AlignmentMode, DepthProcessingConfig

camera = RealSenseD435Camera(
    profile=D435_640X480_30,
    alignment_mode=AlignmentMode.AUTO,
    device_index=0,  # 只在 D435 设备中计数
    # serial_number="实际设备序列号",  # 指定后优先于索引
    depth_processing=DepthProcessingConfig(),  # 默认关闭全部滤波
)
with camera:
    observation = camera.get_latest_observation()
    print(camera.camera_id, observation.rgb.shape, observation.depth_m.shape)
```

`start()` 成功前等待首个完整 RGB-D 观测；每个实例只有一个采集线程。
`stop()` 释放流资源后可以重新启动，帧号仍递增；`close()` 永久关闭。
断流恢复策略与 G305 一致：

- 首次启动在 `startup_timeout_s` 内等待完整帧，单次缺帧不会立即失败。
- 已开始采集后，短于 5 秒的缺帧保留最后观测；调用方可用帧号判断是否更新。
- 连续 5 秒没有发布有效观测，进入 `RECOVERING`，清空普通/滤波对比旧帧。
  此时读取观测返回 `None`，不会因可恢复错误抛出异常。
- 从最后一次发布起算，到 10 秒仍未恢复稳定帧，则停止旧 Pipeline 并重新创建。
  SDK 采集、处理等非配置异常会直接进入恢复态并启动重建。
- 每轮最多重建 3 次，尝试前分别等待 0、1、2 秒；每次有 10 秒等待连续 3 帧。
  原 Pipeline 恢复出图也必须连续 3 帧才重新进入 `STREAMING`，中途缺帧重新计数。
- 重建时按首次连接的序列号找回同一台 D435，重建对齐、点云和滤波对象，重新读取
  标定；不会因 USB 枚举顺序变化连接另一台相机。帧号继续递增。
- 重试耗尽、明确的 Profile/滤波配置错误或首次启动失败才进入 `FAILED`，读取方
  收到公共相机异常，需创建新实例。重连中的临时 SDK 配置解析失败允许有限重试。
- `stop()` / `close()` 会打断重试等待，并在采集线程退出时释放当前 Pipeline。

`RobotSetup` 已支持按 `config/camera/cameras.json` 中的名称创建适配器：
`setup.setup_camera("head")` 默认读取 `d435.json`，返回未启动的 D435；
`setup.setup_camera("left")` 默认读取 `g305.json`，对应左手 G305。每台相机配置可通过
`setup.get_camera_settings(name)` 或 `setup.get_robot_config().cameras[name]` 获取。
名称与型号的对应关系可修改；接入机器人前需确认对应的外参。

## 对齐、坐标与标定

- `AUTO` 和 `SOFTWARE` 都使用 `rs.align(rs.stream.color)`；请求 `HARDWARE` 会报错。
- 当前两组 Profile 的彩色与深度流尺寸相同；软件对齐后的深度尺寸等于 RGB。
- 深度使用帧的 `get_units()` 换算为米。SDK 对齐保留原深度相机的 Z 值；
  适配器使用实际 depth→RGB 外参修正反投影射线的尺度，发布的 `depth_m`
  为彩色光学系 Z，等于点云第三分量。点云按彩色像素排列为 `H×W×3`，
  坐标轴为 X 右、Y 下、Z 前；无效点为 `NaN`，无效深度为 0。
- 数组复制出 SDK 缓冲并设为只读；帧号由模块生成，时间戳保留 SDK 毫秒时间。
- `SensorCalibration` 保存实际原始 RGB/深度流内参及 depth→RGB 外参，
  所以 `depth_intrinsics` 的尺寸可能与 D2C 后的 `depth_m` 不同。
- 公共 `CameraDistortion` 没有畸变模型字段；非零且无法在现有契约中表达的
  逆向/鱼眼系数会明确报错，不能把它们静默作为普通 Brown 系数使用。
  SDK 反投影不支持的 modified Brown 模型也会在采集前拒绝。

## 滤波与对比

滤波在软件 D2C 后执行，不使用会改变分辨率的降采样。空间/时域滤波使用
深度→视差→空间→时域→深度路径，随后孔洞填充和米制阈值过滤。
SDK 阈值作用于原深度相机的 Z，之后再转换为发布观测的彩色光学系 Z。
参考 [SDK 官方滤波说明](https://github.com/realsenseai/librealsense/blob/master/doc/post-processing-filters.md)。

公共配置沿用现有 G305 语义，不能直接把整数传给 RealSense：

| 公共 `hole_filling_mode` | RealSense 原生模式 |
| --- | --- |
| 0：TOP | 无等价模式；启用时拒绝 |
| 1：NEAREST | 2：nearest_from_around |
| 2：FAREST | 1：farest_from_around |

启用孔洞填充时必须显式设置模式 1 或 2。滤波参数依据 SDK 声明的范围检查，
其他约束由 SDK 的 `set_option` 校验；浮点 `step` 不作为强制量化间隔。
超出范围会报错，不会截断或忽略。滤波链每台相机独立，停止重启时重建。

```python
processing = DepthProcessingConfig(
    spatial_enabled=True,
    temporal_enabled=True,
    hole_filling_enabled=True,
    hole_filling_mode=1,
    minimum_depth_m=0.15,
    maximum_depth_m=2.0,
)
with RealSenseD435Camera(
    D435_640X480_30, depth_processing=processing,
    observation_mode="RAW_AND_FILTERED",
) as camera:
    raw, filtered = camera.get_latest_filter_comparison_observations()
    assert raw.frame_id == filtered.frame_id
```

`set_observation_mode()` 仅可在停止状态调用。参数诊断
`get_depth_filter_parameter_schemas()` 返回已启用处理块的 SDK 原生参数描述，
其中孔洞模式的数值是 SDK 语义，不是上表的公共配置值。

## 验证

项目根目录执行：

```powershell
python -m unittest discover -s tests/camera/unit -p "test_realsense*.py"
```

无硬件测试覆盖单位转换、数据所有权、滤波映射、设备/Profile 选择、首帧等待、
重启、断连重建、稳定帧确认、重试耗尽和资源释放。它们不替代 USB 实机测试：连接 D435 后应逐个验证
所需 Profile、RGB-D 对齐、标定和滤波效果，再接入机器人流程。
