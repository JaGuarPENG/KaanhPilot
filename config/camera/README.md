# 多相机配置

`cameras.json` 声明逻辑名称、适配器类型、设备序列号、SDK 设备索引、参数文件与机器人外参编号：

| 名称 | 类型 | 序列号 | 参数文件 | 外参编号 |
| --- | --- | --- | --- | --- |
| `head` | `realsense_d435` | `050122074988` | `d435.json` | 0：eye_to_hand_cam0 |
| `left` | `orbbec_g305` | `CV2T661000PF` | `g305_left.json` | 1：eye_in_hand_cam1 |
| `right` | `orbbec_g305` | `CV2T661000WB` | `g305_right.json` | 2：eye_in_hand_cam2 |

这是当前配置的映射；设备安装位置变化时请核对序列号和外参编号，JSON 不会自动完成标定。
配置 `serial_number` 后，启动时必须找到对应设备；找不到会报错，不会改用
`device_index`。未配置序列号时，`device_index` 才在各 SDK 的设备列表中选择设备。
`device_index` 与 `extrinsic_index` 无关；应用不应使用适配器的私有索引选择外参。

`settings_file` 相对于本目录。当前两台 G305 分别指定独立参数文件；D435 默认
使用 `d435.json`。多台同型号相机可使用不同名称和不同参数文件；同一物理设备
应创建一个实例，由其所有消费者共享，避免重复启动独占 Pipeline。

参数文件分别配置 `profile`、`alignment`、`warmup_seconds`、滤波开关/参数和
深度阈值。省略或同时设为 null 的 `minimum_depth_m` / `maximum_depth_m`
表示关闭深度阈值过滤；不能只给一个边界。

G305 必须配置布尔型 `auto_exposure`。该型号的彩色、IR 和深度自动曝光开关联动。
为 `true` 时，`exposure` 和 `gain` 即使保留在 JSON 中，也不会校验或写入设备；
为 `false` 时，两者都必须为整数，并在关闭自动曝光后写入。启动时按设备实际写入权限、
范围和步长校验。右臂当前使用手动曝光 150、增益 16；左臂启用自动曝光，保留手动值
156 和 16 供以后切换。`color_sharpness` 可设为 0 到 100 的整数，或设为 `null`
以保留设备当前值。修改配置后重启服务生效。

自动曝光下双流彩色画面可能过曝；本配置不会改变设备当前的自动曝光参考源。

| 类型 | 可用 Profile 名称 |
| --- | --- |
| `orbbec_g305` | `848@30`、`848@60`、`1280@30` |
| `realsense_d435` | `640@30`、`1280@6` |

G305 的 `1280@30` 表示 1280×800；D435 的 `1280@6` 表示 1280×720。
D435 两组配置的彩色和深度流采用相同分辨率及帧率。
D435 只接受 `software` / `auto` 对齐；启用孔洞填充时模式只能选
1（最近值）或 2（最远值）。具体参数范围仍由 SDK 在启动时校验。

## 创建两台相机

从项目根目录运行 Python：

```python
from contextlib import ExitStack
from pathlib import Path
import time

from commands.setup import RobotSetup

setup = RobotSetup(Path("config"))
config = setup.get_robot_config()

with ExitStack() as stack:
    # 先登记清理再启动：任意相机启动失败也会关闭此前创建的实例。
    cameras = {}
    for name in ("head", "left"):
        camera = setup.setup_camera(name)
        stack.callback(camera.close)
        cameras[name] = camera
        camera.start()

    time.sleep(max(config.cameras[name].warmup_seconds for name in cameras))
    head = cameras["head"].get_latest_observation()
    left = cameras["left"].get_latest_observation()
    # 恢复期间观测可能为 None；两台相机的最新帧不保证同步曝光。
```

`setup_camera(name)` 返回公共 `Camera` 接口，每次调用创建一个未启动的新实例。
创建本身不连接设备，也不加载厂商 SDK。`get_camera_settings(name)` 可单独获取
预热时间和外参编号。传给 `SnapShotCommand` 时显式使用
`camera_extrinsic_index=config.cameras[name].extrinsic_index`。

旧调用 `setup_camera(0)` / `setup_camera()` 已迁移为 `setup_camera("head")`，
旧的顶层 `RobotConfig.profile/alignment/depth_processing/camera_warmup_seconds`
改为 `RobotConfig.cameras[name]` 中的字段（预热字段为 `warmup_seconds`）。
后端 API 同时启动 `head`（D435）与 `left`（G305），分别提供头部和左手预览。
识别和抓取共享左手 G305 实例，使用 `extrinsic_index=1`；实际安装改变后须重新确认对应外参标定。
独立抓取入口 `two_stage_pick.py` 默认也使用 `left`。
前端代理路径分别为 `/api/cameras/head/frame.jpg` 与 `/api/cameras/left/frame.jpg`。
修改配置后重启后端与前端服务，并刷新页面；右手目前仍显示示意图。
`commands/legacy/yolo_follower.py` 没有提供实时 TCP 位姿，仅接受
`extrinsic_index=0`；手腕相机应使用能提供对应位姿的快照流程。

导出指定相机内参：

```powershell
python -m commands.get_camera_intri --camera left
```

保存到 `intri_param_cam<extrinsic_index>.json`，不同安装位置分别保存内参。

## 独立彩色预览

`get_latest_color_frame()` 返回只包含 `frame_id`、`capture_timestamp_ms` 和只读
`rgb` 数组的 `RGBFrame`；前端 JPEG 使用这个接口。每台相机的 SDK 读取线程
先发布彩色帧，再把完整帧集交给原有处理线程进行软件对齐、深度滤波和点云计算。
待处理位置最多保留一套帧集，新帧替换尚未开始处理的旧帧，避免积压。

`get_latest_observation()` 保持完整 RGB-D 契约，YOLO 和定位继续使用此接口。
同一帧集的预览和完整观测共用帧 ID、时间戳、彩色数据；观测帧 ID 可能跳号。
不能把最新预览图与另一帧观测的深度混用于定位。时域滤波处理的是被选中的帧序列，
高负载下处理帧间隔会变大，应在实际抓取场景中验证滤波效果。

启动仍等待首个完整观测；预览仍等待完整 RGB-D 帧集到达，但不等待软件深度处理。
停止时预览返回 None，SDK 读取异常时清空预览，超过 5 秒没有新彩色帧也返回 None。
Pipeline 重建前先结束读取线程，重建后保留实例内递增帧 ID，避免 JPEG 缓存串帧。
