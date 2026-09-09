"""通过 Modbus TCP 控制 AGV 底盘。

当前模块只实现项目所需的站点导航功能，并尽量保持与
``robot.kaanh_backend.KaanhRobotBackend`` 相似的同步调用方式。

协议约定：

* AGV 是 Modbus TCP 服务端，本模块是客户端；
* 默认地址为 192.168.110.93:9201，Unit ID（pymodbus 3.15 中称
  device_id）默认为 1；
* 按当前项目约定，协议表中的寄存器编号直接传给 pymodbus，暂不做
  ``地址 - 1`` 的转换；
* uint32 均按“高 16 位在前、低 16 位在后”的顺序存放；
* 当前 AGV 固件不校验 00042 心跳，因此本模块不发送心跳。

直接运行本文件只会读取并打印 AGV 当前任务号，不会写寄存器：

    conda run -n pyagent python -m robot.agv_backend
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Sequence

from pymodbus.client import ModbusTcpClient


DEFAULT_IP = "192.168.110.93"
DEFAULT_PORT = 9201
DEFAULT_DEVICE_ID = 1

# 保持寄存器（4x）：向 AGV 下发控制参数。
HOLDING_TASK_ID_HIGH = 50
HOLDING_TASK_ID_LOW = 51
HOLDING_TARGET_STATION = 52
HOLDING_ACTION = 53
HOLDING_START_NAVIGATION = 54
HOLDING_CANCEL_NAVIGATION = 55

# 输入寄存器（3x）：读取 AGV 当前状态。
INPUT_CURRENT_STATION = 1
INPUT_TERMINAL_STATION = 2
INPUT_CURRENT_TASK_ID_HIGH = 3
INPUT_CURRENT_TASK_ID_LOW = 4
INPUT_NAVIGATION_STATUS = 14
INPUT_SYSTEM_STATUS = 32

UINT16_MAX = 0xFFFF
UINT32_MAX = 0xFFFFFFFF


class NavigationStatus(IntEnum):
    """输入寄存器 00014 的导航状态。"""

    INITIAL = 0
    MOVING = 1
    ARRIVED = 2
    CANCELLED = 3
    FAILED = 4


NAVIGATION_STATUS_TEXT = {
    NavigationStatus.INITIAL: "初始状态",
    NavigationStatus.MOVING: "移动中",
    NavigationStatus.ARRIVED: "到达",
    NavigationStatus.CANCELLED: "已取消",
    NavigationStatus.FAILED: "失败",
}

# 输入寄存器 00032 的已知系统状态。未列出的值仍会原样保留，避免因为
# 固件新增状态而导致状态读取失败。
SYSTEM_STATUS_TEXT = {
    1: "初始状态",
    2: "系统空闲",
    3: "系统出错",
    7: "导航执行中",
    8: "检测到障碍，减速",
    10: "遇到障碍，暂停运动",
    18: "无法规划路径",
    19: "到点取消执行中",
    20: "强制取消执行中",
}


class AGVError(RuntimeError):
    """AGV 后端错误基类。"""


class AGVConnectionError(AGVError):
    """AGV 未连接，或 Modbus 通讯失败。"""


class AGVProtocolError(AGVError):
    """AGV 返回 Modbus 异常响应或无效数据。"""


class AGVBusyError(AGVError):
    """AGV 已有导航或取消任务正在执行。"""


@dataclass(frozen=True)
class AGVState:
    """一次状态快照。

    ``current_station`` 对应输入寄存器 00001；``terminal_station``
    对应 00002，后者只有在 AGV 到站并停车后才会上报目标站点。
    """

    current_station: int
    terminal_station: int
    current_task_id: int
    navigation_status: int
    system_status: int

    @property
    def navigation_status_text(self) -> str:
        """返回可读导航状态，同时兼容协议中尚未定义的新状态。"""

        try:
            status = NavigationStatus(self.navigation_status)
        except ValueError:
            return f"未知导航状态({self.navigation_status})"
        return NAVIGATION_STATUS_TEXT[status]

    @property
    def system_status_text(self) -> str:
        """返回输入寄存器 00032 对应的可读说明。"""

        return SYSTEM_STATUS_TEXT.get(
            self.system_status, f"未知系统状态({self.system_status})"
        )

    @property
    def is_busy(self) -> bool:
        """判断当前是否不应接受新的导航任务。

        除导航状态“移动中”外，减速、停障和取消执行中也表示上一任务
        尚未真正结束。这里宁可明确报忙，也不静默覆盖正在执行的任务。
        """

        return self.navigation_status == NavigationStatus.MOVING or self.system_status in {
            7,
            8,
            10,
            19,
            20,
        }


@dataclass(frozen=True)
class NavigationResult:
    """导航下发或导航完成后的结果。"""

    task_id: int
    target_station: int
    current_station: int
    terminal_station: int
    navigation_status: int
    system_status: int
    success: bool | None
    message: str

    @classmethod
    def from_state(
        cls,
        task_id: int,
        target_station: int,
        state: AGVState,
        success: bool | None,
        message: str,
    ) -> "NavigationResult":
        return cls(
            task_id=task_id,
            target_station=target_station,
            current_station=state.current_station,
            terminal_station=state.terminal_station,
            navigation_status=state.navigation_status,
            system_status=state.system_status,
            success=success,
            message=message,
        )


class AGVBackend:
    """AGV Modbus TCP 同步客户端。

    参数：
        ip: AGV 的 IP 地址。
        port: AGV 的 Modbus TCP 端口。
        device_id: Modbus Unit ID；pymodbus 3.15 使用此参数名。
        timeout: 单次 Modbus 通讯超时，单位为秒。

    ``_client`` 只用于注入测试客户端。正常业务代码不需要传入。
    """

    def __init__(
        self,
        ip: str = DEFAULT_IP,
        port: int = DEFAULT_PORT,
        device_id: int = DEFAULT_DEVICE_ID,
        timeout: float = 3.0,
        *,
        _client: Any | None = None,
    ) -> None:
        if not isinstance(ip, str) or not ip.strip():
            raise ValueError("ip 必须是非空字符串")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port 必须是 1 到 65535 之间的整数")
        if (
            isinstance(device_id, bool)
            or not isinstance(device_id, int)
            or not 0 <= device_id <= 255
        ):
            raise ValueError("device_id 必须是 0 到 255 之间的整数")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        self.ip = ip.strip()
        self.port = port
        self.device_id = device_id
        self.timeout = float(timeout)
        self.client = _client or ModbusTcpClient(
            self.ip,
            port=self.port,
            timeout=self.timeout,
        )
        self.is_connected = False

        # 一个 AGV 同一时刻只能接受一条导航任务。此锁保证本后端中的多个
        # 调用线程不会同时读取同一个旧任务号并下发重复的新任务号。
        self._navigation_lock = threading.Lock()
        self._last_issued_task_id: int | None = None

    def connect(self) -> bool:
        """连接 AGV，成功返回 True，失败返回 False。"""

        try:
            self.is_connected = bool(self.client.connect())
        except Exception as error:
            self.is_connected = False
            print(f"[AGV连接] 失败: {error}")
            return False

        if self.is_connected:
            print(f"[AGV连接] 成功: {self.ip}:{self.port}, Unit ID={self.device_id}")
        else:
            print(f"[AGV连接] 失败: {self.ip}:{self.port}")
        return self.is_connected

    def close(self) -> None:
        """关闭 Modbus TCP 连接。"""

        try:
            self.client.close()
        finally:
            self.is_connected = False

    def __enter__(self) -> "AGVBackend":
        if not self.connect():
            raise AGVConnectionError(f"无法连接 AGV: {self.ip}:{self.port}")
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 基础 Modbus 读写接口
    # ------------------------------------------------------------------

    def read_input_registers(self, address: int, count: int = 1) -> list[int]:
        """读取一个或多个输入寄存器（3x）。"""

        self._validate_address_and_count(address, count)
        self._ensure_connected()
        try:
            response = self.client.read_input_registers(
                address,
                count=count,
                device_id=self.device_id,
            )
        except Exception as error:
            raise AGVConnectionError(
                f"读取输入寄存器 {address} 失败: {error}"
            ) from error
        return self._register_values(response, count, f"读取输入寄存器 {address}")

    def read_input_register(self, address: int) -> int:
        """读取单个输入寄存器（3x）。"""

        return self.read_input_registers(address, 1)[0]

    def read_holding_registers(self, address: int, count: int = 1) -> list[int]:
        """读取一个或多个保持寄存器（4x）。"""

        self._validate_address_and_count(address, count)
        self._ensure_connected()
        try:
            response = self.client.read_holding_registers(
                address,
                count=count,
                device_id=self.device_id,
            )
        except Exception as error:
            raise AGVConnectionError(
                f"读取保持寄存器 {address} 失败: {error}"
            ) from error
        return self._register_values(response, count, f"读取保持寄存器 {address}")

    def read_holding_register(self, address: int) -> int:
        """读取单个保持寄存器（4x）。"""

        return self.read_holding_registers(address, 1)[0]

    def write_holding_register(self, address: int, value: int) -> None:
        """写入单个保持寄存器（功能码 06）。"""

        self._validate_address_and_count(address, 1)
        self._validate_uint16(value, "value")
        self._ensure_connected()
        try:
            response = self.client.write_register(
                address,
                value,
                device_id=self.device_id,
            )
        except Exception as error:
            raise AGVConnectionError(
                f"写入保持寄存器 {address} 失败: {error}"
            ) from error
        self._check_response(response, f"写入保持寄存器 {address}")

    def write_holding_registers(self, address: int, values: Sequence[int]) -> None:
        """连续写入多个保持寄存器（功能码 16）。"""

        if isinstance(values, (str, bytes)):
            raise ValueError("values 必须是 uint16 序列")
        values = list(values)
        if not values:
            raise ValueError("values 不能为空")
        self._validate_address_and_count(address, len(values))
        for index, value in enumerate(values):
            self._validate_uint16(value, f"values[{index}]")

        self._ensure_connected()
        try:
            response = self.client.write_registers(
                address,
                values,
                device_id=self.device_id,
            )
        except Exception as error:
            raise AGVConnectionError(
                f"连续写入保持寄存器 {address} 失败: {error}"
            ) from error
        self._check_response(response, f"连续写入保持寄存器 {address}")

    # ------------------------------------------------------------------
    # 任务号和导航参数基础接口
    # ------------------------------------------------------------------

    @staticmethod
    def split_uint32(value: int) -> tuple[int, int]:
        """将 uint32 拆成“高 16 位、低 16 位”。"""

        AGVBackend._validate_uint32(value, "value")
        return (value >> 16) & UINT16_MAX, value & UINT16_MAX

    @staticmethod
    def combine_uint32(high: int, low: int) -> int:
        """将高、低两个 uint16 合并成 uint32。"""

        AGVBackend._validate_uint16(high, "high")
        AGVBackend._validate_uint16(low, "low")
        return (high << 16) | low

    def get_current_task_id(self) -> int:
        """读取输入寄存器 00003、00004，并返回完整 uint32 任务号。"""

        high, low = self.read_input_registers(INPUT_CURRENT_TASK_ID_HIGH, 2)
        return self.combine_uint32(high, low)

    def print_current_task_id(self) -> int:
        """读取并打印 AGV 当前任务号，返回完整 uint32 值。

        这是用于现场验证任务完成后任务号是否保留的专用接口。打印结果同时
        给出十进制、高低 16 位和十六进制，便于与寄存器工具交叉核对。
        """

        task_id = self.get_current_task_id()
        high, low = self.split_uint32(task_id)
        print(
            f"[AGV任务号] 十进制={task_id}, 高16位={high}, "
            f"低16位={low}, 十六进制=0x{task_id:08X}"
        )
        return task_id

    def set_task_id(self, task_id: int) -> None:
        """将任务号写入保持寄存器 00050、00051。

        两个寄存器通过一次“写多个寄存器”请求一起发送，避免 AGV 在高、
        低 16 位只写入一半时观察到不完整任务号。
        """

        high, low = self.split_uint32(task_id)
        self.write_holding_registers(HOLDING_TASK_ID_HIGH, [high, low])

    def set_target_station(self, station_id: int) -> None:
        """把正整数站点 ID 写入保持寄存器 00052。"""

        self._validate_station_id(station_id)
        self.write_holding_register(HOLDING_TARGET_STATION, station_id)

    def set_action(self, action_id: int = 0) -> None:
        """设置到站动作 00053；普通导航应明确写 0。"""

        self._validate_uint16(action_id, "action_id")
        self.write_holding_register(HOLDING_ACTION, action_id)

    def start_navigation(self) -> None:
        """向保持寄存器 00054 写 1，提交已经准备好的导航参数。"""

        self.write_holding_register(HOLDING_START_NAVIGATION, 1)

    def cancel_navigation(self, force: bool = False) -> None:
        """取消当前任务。

        ``force=False`` 写 1，表示到点后取消；``force=True`` 写 2，表示
        原地强制取消。
        """

        if not isinstance(force, bool):
            raise ValueError("force 必须是 bool")
        self.write_holding_register(HOLDING_CANCEL_NAVIGATION, 2 if force else 1)

    # ------------------------------------------------------------------
    # 状态读取接口
    # ------------------------------------------------------------------

    def get_state(self) -> AGVState:
        """一次读取输入寄存器 00001～00032，生成一致的状态快照。"""

        registers = self.read_input_registers(INPUT_CURRENT_STATION, 32)

        def value(address: int) -> int:
            return registers[address - INPUT_CURRENT_STATION]

        return AGVState(
            current_station=value(INPUT_CURRENT_STATION),
            terminal_station=value(INPUT_TERMINAL_STATION),
            current_task_id=self.combine_uint32(
                value(INPUT_CURRENT_TASK_ID_HIGH),
                value(INPUT_CURRENT_TASK_ID_LOW),
            ),
            navigation_status=value(INPUT_NAVIGATION_STATUS),
            system_status=value(INPUT_SYSTEM_STATUS),
        )

    def get_current_station(self) -> int:
        """读取输入寄存器 00001：机器人当前点。"""

        return self.read_input_register(INPUT_CURRENT_STATION)

    def get_terminal_station(self) -> int:
        """读取输入寄存器 00002：停车后上报的终到站点。"""

        return self.read_input_register(INPUT_TERMINAL_STATION)

    def get_navigation_status(self) -> int:
        """读取输入寄存器 00014：导航状态。"""

        return self.read_input_register(INPUT_NAVIGATION_STATUS)

    def get_system_status(self) -> int:
        """读取输入寄存器 00032：系统状态。"""

        return self.read_input_register(INPUT_SYSTEM_STATUS)

    # ------------------------------------------------------------------
    # 集成导航接口
    # ------------------------------------------------------------------

    def navigate_to(
        self,
        station_id: int,
        *,
        wait: bool = True,
        poll_interval: float = 0.2,
    ) -> NavigationResult:
        """生成新任务号并导航到指定站点。

        下发顺序为：任务号 → 站点 → 动作 0 → 启动 1。启动寄存器一定
        最后写，避免 AGV 在导航参数尚未完整时开始任务。

        当前实现不设置总等待超时，也不会自动取消任务。``wait=False``
        时仅表示 Modbus 写请求已经完成，``success`` 返回 None；调用方可
        保存 ``task_id``，随后使用 :meth:`wait_for_arrival` 等待到站。
        """

        self._validate_station_id(station_id)
        self._validate_poll_interval(poll_interval)

        with self._navigation_lock:
            before = self.get_state()
            if before.is_busy:
                raise AGVBusyError(
                    "AGV正在执行任务，"
                    f"导航状态={before.navigation_status_text}，"
                    f"系统状态={before.system_status_text}"
                )

            task_id = self._next_task_id(before.current_task_id)
            self.set_task_id(task_id)
            self.set_target_station(station_id)
            # self.set_action(0)
            self.start_navigation()
            self._last_issued_task_id = task_id

        if wait:
            return self.wait_for_arrival(task_id, station_id, poll_interval=poll_interval)

        return NavigationResult.from_state(
            task_id,
            station_id,
            before,
            success=None,
            message="导航参数和启动指令已下发，尚未等待到站",
        )

    def wait_for_arrival(
        self,
        task_id: int,
        station_id: int,
        *,
        poll_interval: float = 0.2,
    ) -> NavigationResult:
        """持续轮询，直到指定任务到达、取消或失败。

        00014 的“到达”会一直保留到下一任务，因此不能只判断状态等于 2。
        本方法要求“当前任务号、本次任务号”匹配，并同时校验终到站点，
        从而避免把上一任务残留的到达状态误判为本次到达。
        """

        self._validate_uint32(task_id, "task_id")
        self._validate_station_id(station_id)
        self._validate_poll_interval(poll_interval)
        accepted = False

        while True:
            state = self.get_state()

            if state.current_task_id == task_id:
                accepted = True

                if state.navigation_status == NavigationStatus.ARRIVED:
                    if state.terminal_station == station_id:
                        return NavigationResult.from_state(
                            task_id,
                            station_id,
                            state,
                            success=True,
                            message=f"AGV已到达站点 {station_id}",
                        )
                    # 状态已经是到达，但终到站点不匹配，继续轮询一个周期。
                    # 某些控制器可能先更新状态、再更新终到站点。

                elif state.navigation_status == NavigationStatus.CANCELLED:
                    return NavigationResult.from_state(
                        task_id,
                        station_id,
                        state,
                        success=False,
                        message=(
                            "导航任务已取消，"
                            f"系统状态={state.system_status_text}"
                        ),
                    )

                elif state.navigation_status == NavigationStatus.FAILED:
                    return NavigationResult.from_state(
                        task_id,
                        station_id,
                        state,
                        success=False,
                        message=(
                            "导航任务失败，"
                            f"系统状态={state.system_status_text}"
                        ),
                    )

            elif accepted and state.current_task_id != 0:
                # 单写入端模型下，本任务被接受后不应突然出现其他任务号。
                # 返回明确失败，避免调用方继续无限等待一个已被替换的任务。
                return NavigationResult.from_state(
                    task_id,
                    station_id,
                    state,
                    success=False,
                    message=(
                        f"等待中的任务 {task_id} 已被任务 "
                        f"{state.current_task_id} 替换"
                    ),
                )

            time.sleep(poll_interval)

    def _next_task_id(self, current_task_id: int) -> int:
        """根据 AGV 当前任务号生成一个不同的新 uint32 任务号。"""

        self._validate_uint32(current_task_id, "current_task_id")
        candidate = (current_task_id + 1) & UINT32_MAX
        if candidate == 0:
            candidate = 1

        # 若固件在任务结束后把输入任务号清零，本实例生命周期内仍要避免
        # 重复使用刚下发的任务号。跨后端重启是否需要持久化，将由现场读取
        # 00003/00004 的测试结果决定。
        if candidate == self._last_issued_task_id:
            candidate = (candidate + 1) & UINT32_MAX
            if candidate == 0:
                candidate = 1
        return candidate

    # ------------------------------------------------------------------
    # 内部校验和响应处理
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise AGVConnectionError("AGV未连接，请先调用 connect()")

    @staticmethod
    def _check_response(response: Any, operation: str) -> None:
        if response is None:
            raise AGVProtocolError(f"{operation}未收到响应")
        is_error = getattr(response, "isError", None)
        if callable(is_error) and is_error():
            raise AGVProtocolError(f"{operation}返回异常响应: {response}")

    @classmethod
    def _register_values(
        cls, response: Any, expected_count: int, operation: str
    ) -> list[int]:
        cls._check_response(response, operation)
        registers = getattr(response, "registers", None)
        if not isinstance(registers, list) or len(registers) < expected_count:
            raise AGVProtocolError(
                f"{operation}返回寄存器数量不足，期望 {expected_count}，"
                f"实际 {0 if registers is None else len(registers)}"
            )
        values = [int(value) for value in registers[:expected_count]]
        for index, value in enumerate(values):
            cls._validate_uint16(value, f"响应寄存器[{index}]")
        return values

    @staticmethod
    def _validate_address_and_count(address: int, count: int) -> None:
        if isinstance(address, bool) or not isinstance(address, int) or address < 0:
            raise ValueError("address 必须是非负整数")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("count 必须是正整数")
        if address + count - 1 > UINT16_MAX:
            raise ValueError("寄存器范围超出 uint16 地址范围")

    @staticmethod
    def _validate_uint16(value: int, name: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= UINT16_MAX
        ):
            raise ValueError(f"{name} 必须是 0 到 {UINT16_MAX} 之间的整数")

    @staticmethod
    def _validate_uint32(value: int, name: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= UINT32_MAX
        ):
            raise ValueError(f"{name} 必须是 0 到 {UINT32_MAX} 之间的整数")

    @classmethod
    def _validate_station_id(cls, station_id: int) -> None:
        cls._validate_uint16(station_id, "station_id")
        if station_id == 0:
            raise ValueError("station_id 必须是正整数")

    @staticmethod
    def _validate_poll_interval(poll_interval: float) -> None:
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or poll_interval <= 0
        ):
            raise ValueError("poll_interval 必须是大于 0 的秒数")


def _main() -> int:
    """命令行只读测试入口：连接 AGV 并打印当前任务号。"""

    parser = argparse.ArgumentParser(description="读取并打印 AGV 当前任务号")
    parser.add_argument("--ip", default=DEFAULT_IP, help="AGV IP 地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Modbus TCP 端口")
    parser.add_argument(
        "--device-id",
        type=int,
        default=DEFAULT_DEVICE_ID,
        help="Modbus Unit ID，默认 1",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="通讯超时秒数")
    args = parser.parse_args()

    agv = AGVBackend(
        ip=args.ip,
        port=args.port,
        device_id=args.device_id,
        timeout=args.timeout,
    )
    if not agv.connect():
        return 1
    try:
        agv.print_current_task_id()
    except AGVError as error:
        print(f"[AGV任务号] 读取失败: {error}")
        return 2
    finally:
        agv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
