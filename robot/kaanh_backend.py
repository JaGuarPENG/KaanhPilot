from typing import Sequence
import math
import websocket
import struct
import json
import hashlib
import time

from robot.follower_udp_client import FollowerUdpClient
from robot.robot_state import RobotState, parse_robot_state


DEFAULT_MONITOR_PORT = 5888
DEFAULT_CONTROL_PORT = 5999
DEFAULT_UDP_PORT = 9998


# Controller order: arm1(7), arm2(7), waist(4), head_yaw(1), head_pitch(1).
MODEL_JOINT_COUNTS = (7, 7, 4, 1, 1)
MODEL_JOINT_TARGET_TYPES = (
    "OffsetSevenAxis_JointTarget",
    "OffsetSevenAxis_JointTarget",
    "AbenicsModel_JointTarget",
    "ExAxisModel_JointTarget",
    "ExAxisModel_JointTarget",
)
TOTAL_JOINT_COUNT = sum(MODEL_JOINT_COUNTS)

# Controller ``mvl`` target order: arm1 TCP/axis, arm2 TCP/axis, waist,
# head_yaw, head_pitch.  Only arm TCP targets are commanded by this backend.
MOVEL_TARGET_COUNTS = (6, 1, 6, 1, 3, 1, 1)
MOVEL_TARGET_TYPES = (
    "EE_XYZABC",
    "EE_A",
    "EE_XYZABC",
    "EE_A",
    "EE_ABC",
    "EE_A",
    "EE_A",
)
MOVEL_ARM_MODEL_IDS = (0, 1)
MOVEL_OPTIONS = "--vel=Speed#7#{Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{180.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{180.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{180.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{180.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{180.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{50.000000}},Speed{DOUBLE{10.000000},DOUBLE{100.000000},DOUBLE{50.000000}}} --zone=Zone#7#{Zone{DOUBLE{0.000000},DOUBLE{0.000000}},Zone{DOUBLE{0.000000},DOUBLE{0.000000}},Zone{DOUBLE{0.000000},DOUBLE{0.000000}},Zone{DOUBLE{1},DOUBLE{0.000000}},Zone{DOUBLE{0.000000},DOUBLE{0.000000}},Zone{DOUBLE{0.000000},DOUBLE{0.000000}},Zone{DOUBLE{0.000000},DOUBLE{0.000000}}} --pos_offset=Offset{OFF_XYZABC{DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},BOOL{false}},OFF_A{DOUBLE{0.000000},BOOL{false}},OFF_XYZABC{DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},BOOL{false}},OFF_A{DOUBLE{0.000000},BOOL{false}},OFF_ABC{DOUBLE{0.000000},DOUBLE{0.000000},DOUBLE{0.000000},BOOL{false}},OFF_A{DOUBLE{0.000000},BOOL{false}},OFF_A{DOUBLE{0.000000},BOOL{false}}} --ch=-1 --tg=2 --reinit=1"



class RobotCommandError(RuntimeError):
    """The controller did not complete a robot command successfully."""


class TargetUnreachableError(RobotCommandError):
    """An MvL command was explicitly rejected because its target is unreachable."""


class KaanhRobotBackend:
    """Kaanh 机器人后端客户端
    
    输入参数表：
    - ip: 机器人 IP 地址
    - port: WebSocket 控制端口 (默认 5999)
    - udp_port: UDP 端口 (默认 9998)
    - timeout: 连接超时时间 (默认 10 秒)
    
    """


    def __init__(
        self,
        ip,
        port=DEFAULT_CONTROL_PORT,
        udp_port=DEFAULT_UDP_PORT,
        timeout=10,
    ):
        self.uri = f"ws://{ip}:{port}"
        self.ws = None
        self.follower_udp = FollowerUdpClient(ip, port=udp_port)
        self.is_connected = False
        self.timeout = timeout  # 保存超时时间设置
        self.follower_state = False  # 记录follower模式是否已启动
        self.follower_start_position = None
        self.follower_start_pq = None
        self.follower_target_pq = None
        self.current_state = RobotState()  # 保存当前机器人状态
        self.error_code = None  # 保存当前错误码
        self.robot_status = None  # 保存当前机器人状态描述
        

    def connect(self):
        try:
            self.ws = websocket.WebSocket()
            # 建立连接
            self.ws.connect(self.uri, timeout=self.timeout) 
            self.is_connected = True
            print(f"[连接] 成功: {self.uri}")
            return True
        except Exception as e:
            print(f"[连接] 失败: {e}")
            return False
    
    def get_status(self):
        resp = self._send_raw_command("get", need_reply=True, need_check=False)
        # print(f"[状态] 原始响应: {resp}")
        data = self._parse_json(resp)
        for _ in range(3):
            if self._is_status_response(data):
                return data
            if not self._is_empty_ack(data):
                return data
            print("[DEBUG get_status] skipped stale empty ACK")
            try:
                data = self._parse_json(self._recv_payload(timeout=0.2))
            except websocket.WebSocketTimeoutException:
                return None
        return data
    
    def get_robot_state(self) -> RobotState:
        """获取当前机器人状态，返回 RobotState 实例
        
        RoboState结构体包括：
        - joints_deg: 关节角度，单位为度
        - actual_joints_deg: 实际关节角度，单位为度
        - tcp_position: TCP位置，单位为米
        - tcp_pe: TCP位姿，321欧拉角表示，单位为米和弧度
        - tcp_pq: TCP位姿，四元数表示，单位为米和弧度，顺序为 [x, y, z, qx, qy, qz, qw]
        - follower_active: 是否处于follower模式
        - follower_mode: follower模式名称
        - moving: 机器人是否在移动
        - error_code: 错误码
        - has_error: 是否存在错误
        - driver_error_codes: 驱动错误码列表
        - robot_status: 机器人状态描述
        - robot_motion: 机器人运动状态描述
        - op_mode: 操作模式
        - activated: 是否已激活
        - jog_coordinate: 当前JOG坐标系
        - timestamp: 状态更新时间戳
        - raw: 原始状态字典，包含所有未解析的字段

        """
        raw_status = self.get_status()
        self.current_state = parse_robot_state(raw_status)
        self.error_code = self.current_state.error_code
        self.robot_status = self.current_state.robot_status
        return self.current_state

    def close(self):
        self.follower_udp.close()
        if self.ws: self.ws.close()
        self.is_connected = False  
        self.follower_state = False  # 重置follower状态

    def login(self, user="Engineer", pwd="000000"):
        pwd_md5 = hashlib.md5(pwd.encode('utf-8')).hexdigest()
        self._send_raw_command(f"login --user={user} --pwd={pwd_md5}", need_reply=True, need_check=False)


    def manual_enable(self):
        self._send_raw_command("manual_en")
        
    def manual_disable(self):
        self._send_raw_command("manual_ds")

    def auto_enable(self):
        self._send_raw_command("en")

    def auto_disable(self):
        self._send_raw_command("ds")


    def movej(self, joints_deg: Sequence[float]):
        """发送全身关节运动指令。

        ``joints_deg`` 必须包含 20 个以度为单位的关节角，且顺序固定为
        ``[arm1(7), arm2(7), waist(4), head_yaw(1), head_pitch(1)]``。
        控制器已支持的多模型 ``manual_mvaj`` 格式不发送速度项。
        """
        values = self._joint_values(joints_deg, TOTAL_JOINT_COUNT, "movej")
        response = self._send_raw_command(self._build_movej_command(values))
        return self._validate_command_response(response, "MoveJ")

    def movej_model(self, model_id: int, joints_deg: Sequence[float]):
        """移动一个控制器模型，其余模型保持读取到的实际关节角。

        ``model_id`` 的含义为：0=臂1、1=臂2、2=腰部、3=头偏航、4=头俯仰。
        ``joints_deg`` 的长度必须分别为 7、7、4、1、1，单位为度。
        控制器报文仍包含全部五个模型目标，因此本方法会先读取完整的 20 轴
        当前目标关节角，再仅替换请求模型对应的切片。
        """
        if isinstance(model_id, bool) or not isinstance(model_id, int):
            raise ValueError("model_id 必须是 0 到 4 的整数")
        if not 0 <= model_id < len(MODEL_JOINT_COUNTS):
            raise ValueError("model_id 必须在 0 到 4 之间")

        values = self._joint_values(
            joints_deg, MODEL_JOINT_COUNTS[model_id], "movej_model"
        )
        state = self.get_robot_state()
        current = state.actual_joints_deg
        full_target = self._joint_values(
            current, TOTAL_JOINT_COUNT, "当前机器人关节状态"
        )
        start = sum(MODEL_JOINT_COUNTS[:model_id])
        full_target[start : start + len(values)] = values
        return self.movej(full_target)

    @staticmethod
    def _joint_values(
        joints_deg: Sequence[float] | None, expected_count: int, label: str
    ) -> list[float]:
        """Validate and normalize one controller-order joint vector."""
        if joints_deg is None or isinstance(joints_deg, (str, bytes)):
            raise ValueError(f"{label} 需要 {expected_count} 个关节角度")
        try:
            values = [float(value) for value in joints_deg]
        except TypeError as error:
            raise ValueError(f"{label} 需要可迭代的关节角度") from error
        except (ValueError, OverflowError) as error:
            raise ValueError(f"{label} 包含非数值关节角度") from error
        if len(values) != expected_count:
            raise ValueError(f"{label} 需要 {expected_count} 个关节角度，收到 {len(values)} 个")
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{label} 包含非有限关节角度")
        return values

    @staticmethod
    def _build_movej_command(joints_deg: Sequence[float]) -> str:
        """Build the validated multi-model ``manual_mvaj`` command."""
        values = KaanhRobotBackend._joint_values(
            joints_deg, TOTAL_JOINT_COUNT, "movej"
        )
        targets = []
        offset = 0
        for target_type, joint_count in zip(
            MODEL_JOINT_TARGET_TYPES, MODEL_JOINT_COUNTS
        ):
            group = values[offset : offset + joint_count]
            encoded = ",".join(f"DOUBLE{{{value:.6f}}}" for value in group)
            targets.append(f"{target_type}{{{encoded}}}")
            offset += joint_count
        return f"manual_mvaj --pos=JointTarget{{{','.join(targets)}}}"

    def movel(self, arm1_pe: Sequence[float], arm2_pe: Sequence[float]):
        """同步移动双臂。

        两个参数都是 ``[x, y, z, a, b, c]``，位置单位为 mm、姿态单位为度。
        腰部和头部，以及两臂的附属 ``EE_A`` 信息从当前控制器状态读取并
        原样回填到完整的七段 ``mvl`` 报文中。
        """
        targets = self._current_movel_targets()
        targets[0] = self._joint_values(arm1_pe, 6, "arm1_pe")
        targets[2] = self._joint_values(arm2_pe, 6, "arm2_pe")
        response = self._send_raw_command(self._build_movel_command(targets))
        return self._validate_movel_response(response)

    def movel_model(self, model_id: int, pe: Sequence[float]):
        """移动指定手臂。

        ``model_id`` 只能是 0（臂1）或 1（臂2）；``pe`` 为该臂的六维
        ``[x, y, z, a, b, c]``，位置单位 mm、姿态单位度。其他六段目标均从
        当前控制器状态保留。
        """
        if isinstance(model_id, bool) or model_id not in MOVEL_ARM_MODEL_IDS:
            raise ValueError("movel_model 的 model_id 只能是 0（臂1）或 1（臂2）")
        targets = self._current_movel_targets()
        targets[model_id * 2] = self._joint_values(pe, 6, "pe")
        response = self._send_raw_command(self._build_movel_command(targets))
        return self._validate_movel_response(response)

    def _validate_movel_response(self, response):
        """Apply MvL-only controller response semantics."""

        if response is None:
            raise RobotCommandError("MvL 未收到控制器响应，执行结果不确定")
        data = self._parse_json(response)
        if not isinstance(data, dict):
            raise RobotCommandError("MvL 收到无法解析的控制器响应")
        return_code = data.get("ret_code")
        if return_code == 10000:
            raise TargetUnreachableError("MvL 目标点不可达")
        if return_code != 0:
            message = data.get("ret_msg") or "未知控制器错误"
            raise RobotCommandError(
                f"MvL 执行失败，ret_code={return_code}: {message}"
            )
        return response

    def _validate_command_response(self, response, command_name: str):
        """Validate a non-MvL command without target-unreachable semantics."""

        if response is None:
            raise RobotCommandError(
                f"{command_name} 未收到控制器响应，执行结果不确定"
            )
        data = self._parse_json(response)
        if not isinstance(data, dict):
            raise RobotCommandError(f"{command_name} 收到无法解析的控制器响应")
        return_code = data.get("ret_code")
        if return_code != 0:
            message = data.get("ret_msg") or "未知控制器错误"
            raise RobotCommandError(
                f"{command_name} 执行失败，ret_code={return_code}: {message}"
            )
        return response

    def _current_movel_targets(self) -> list[list[float]]:
        """Read the seven controller PE groups required by an ``mvl`` command."""
        state = self.get_robot_state()
        if len(state.models) < len(MODEL_JOINT_COUNTS):
            raise ValueError("当前机器人状态不包含全部五个模型")

        arm1, arm2, waist, head_yaw, head_pitch = state.models[:5]
        raw_targets = (
            arm1.pe,
            arm1.axis_pe,
            arm2.pe,
            arm2.axis_pe,
            waist.pe,
            head_yaw.pe,
            head_pitch.pe,
        )
        return [
            self._joint_values(values, count, f"当前 PE[{index}]")
            for index, (values, count) in enumerate(zip(raw_targets, MOVEL_TARGET_COUNTS))
        ]

    @staticmethod
    def _build_movel_command(targets: Sequence[Sequence[float]]) -> str:
        """Build the verified seven-target ``mvl`` command."""
        if len(targets) != len(MOVEL_TARGET_COUNTS):
            raise ValueError(f"mvl 需要 {len(MOVEL_TARGET_COUNTS)} 段目标")

        encoded_targets = []
        for index, (target, target_type, value_count) in enumerate(
            zip(targets, MOVEL_TARGET_TYPES, MOVEL_TARGET_COUNTS)
        ):
            values = KaanhRobotBackend._joint_values(
                target, value_count, f"mvl PE[{index}]"
            )
            encoded = ",".join(f"DOUBLE{{{value:.6f}}}" for value in values)
            encoded_targets.append(
                f"{target_type}{{{encoded},INT32{{0}},INT32{{0}},BOOL{{false}}}}"
            )
        return f"mvl --pe=RobotTarget{{{','.join(encoded_targets)}}} {MOVEL_OPTIONS}"

    def set_jog_vel(self, percent):
        """设置JOG速度百分比 (0-100)"""
        percent = max(0, min(100, percent))
        self._send_raw_command(f"set_jog_vel --vel_percent={percent}")

    def set_pgm_vel(self, percent):
        """设置程序速度百分比 (0-100)"""
        percent = max(0, min(100, percent))
        self._send_raw_command(f"set_pgm_vel --vel_percent={percent}")

    def set_jog_coordinate(self):
        """设置点动坐标系为工具"""
        self._send_raw_command(f"set_jog_coordinate --tool")

    def set_tool(self, tool_id=0):
        """设置工具坐标系id"""
        self._send_raw_command(f"set_tool --index=[{tool_id}]")

    def set_op_mode(self, op_mode="manual"):
        """设置操作模式"""
        self._send_raw_command(f"set_op_mode --{op_mode}")

    def set_confdata_state(self, data="false"):
        """设置配置数据状态, 初始化时需要关闭否则会校验象限"""
        self._send_raw_command(f"set_confdata_state --data={data}")

    # follower_cart指令会在结束时返回一个空的ACK，表示动作已完成。为了避免后续指令被这个空ACK干扰，需要在发送follower_cart指令后清空所有空ACK。
    def start_follower(self):
        """启动follower_cart模式"""
        if not self.follower_udp.connect():
            print("[Follow] UDP连接失败，无法启动follower模式")
            self.follower_state = False
            return False
        else:
            time.sleep(0.5)  # 等待UDP连接稳定
            state = self.get_robot_state()
            robot_start_pq = state.tcp_pq
            if robot_start_pq is None:
                print("[Follow] 无法获取机器人当前位姿，无法启动follower模式")
                self.follower_udp.close()
                self.follower_state = False
                return False

            self.follower_start_pq = state.tcp_pq
            self.follower_target_pq = None
            self._send_raw_command("follower_cart", need_reply=False, need_check=True)
            self.follower_state = True
            return True

    # 控制器处做了滤波，需要一直发目标点才能稳定收敛到期望位置，不能只发一条。相对的是启动follower时的位姿
    def send_pose_pq(self, pose_pq: Sequence[float]):
        """发送follower_cart模式下的位姿，位置单位为毫米，四元数顺序为 qx,qy,qz,qw。"""
        if not self.follower_state:
            print("[Follow] follower模式未启动，无法发送位姿")
            return False
        if not self.follower_udp.is_connected:
            self.follower_state = False
            print("[Follow] follower UDP未连接，无法发送位姿")
            return False
        
        #先做位置单位转换，mm -> m
        pose_pq = [pose_pq[0]/1000.0, pose_pq[1]/1000.0, pose_pq[2]/1000.0] + list(pose_pq[3:7])
        #再做数据清理，去掉接近0的值
        pose_pq = self._clear_data(pose_pq, tolerance=1e-5)
        """发送follower_cart模式下的位姿"""    
        return self.follower_udp.send_pose_quaternion(
        pose_pq[0], pose_pq[1], pose_pq[2], pose_pq[3], pose_pq[4], pose_pq[5], pose_pq[6]
        )
    
   
    def stop_follower(self):
        """停止follower模式"""
        self._send_raw_command("stop_follower", need_reply=True, need_check=False)
        self._drain_empty_acks()
        self.follower_udp.close()
        self.follower_state = False



#################### 灵巧手 #########################

    def hand_en(self,id=15):
        """灵巧手使能"""
        self._send_raw_command(f"hand_en --slave_id={id}")
        time.sleep(0.1)

    def hand_home(self, id=15):
        """灵巧手回零"""
        self._send_raw_command(f"hand_home --slave_id={id}")
        time.sleep(0.1)

    def hand_move(self, id=15, j1=1000, j2=1000, j3=1000, j4=1000, j5=1000, j6=1000, vel=10000, cur=1000):
        """灵巧手移动到指定位置
        - j1:大拇指侧摆
        - j2:大拇指弯曲
        - j3:食指弯曲
        - j4:中指弯曲
        - j5:无名指弯曲
        - j6:小拇指弯曲
        - vel:速度
        - cur:电流
        """
        self._send_raw_command(f"hand_mv --slave_id={id} --j1={j1} --j2={j2} --j3={j3} --j4={j4} --j5={j5} --j6={j6} --vel={vel} --cur={cur}")
        time.sleep(0.1)

#################### 内部方法 #########################

    def _pack_header(self, msg_len):
        return struct.pack('<IIQqqq', msg_len, 0x01, 0x1000, 0xA1B2C3D4, 0, 0)

    def _send_raw_command(self, cmd_str, need_reply=True, need_check=True):
        """发送原始指令，返回原始响应 (字节)"""
        if not self.is_connected: 
            raise RuntimeError("[backend] WebSocket未连接，无法发送指令")
        if need_check:
            self.get_robot_state()  # 每次发送指令前获取最新状态，更新 error_code 和 robot_status
            if self.error_code not in (None, 0):
                raise RuntimeError(f"[backend] 机器人存在错误，错误码={self.error_code}，状态={self.robot_status}")
            self._drain_empty_acks()  # 清空可能存在的空ACK，避免干扰后续指令
        try:
            payload = cmd_str.encode('utf-8')
            full_packet = self._pack_header(len(payload)) + payload
            self.ws.send(full_packet, opcode=websocket.ABNF.OPCODE_BINARY)
            if not need_reply:
                return None
            # 阻塞等待接收
            return self._recv_payload()
            # resp = self._recv_payload()
            # data = self._parse_json(self._recv_payload())
            # if need_check and not self._is_status_response(data) and not self._is_empty_ack(data):
            #     raise RuntimeError(f"[backend] 指令发送成功，但收到异常响应: {resp}")
            # return data

            


        except websocket.WebSocketTimeoutException:
            print(f"[backend] 指令发送成功，但在 {self.timeout}秒内未收到回复 (可能动作时间过长)")
            return None
        except Exception as e:
            print(f"[backend] 异常: {e}")
            return None
    
    def _recv_payload(self, timeout=None):
        if self.ws is None:
            return None

        old_timeout = None
        if timeout is not None:
            old_timeout = self.ws.gettimeout()
            self.ws.settimeout(timeout)

        try:
            resp = self.ws.recv()
            return resp[40:] if len(resp) >= 40 else None
        finally:
            if timeout is not None:
                self.ws.settimeout(old_timeout)

    def _drain_empty_acks(self, max_reads=3, timeout=0.2):
        for _ in range(max_reads):
            try:
                data = self._parse_json(self._recv_payload(timeout=timeout))
            except websocket.WebSocketTimeoutException:
                return
            if not self._is_empty_ack(data):
                return

    def _is_empty_ack(self, data):
        return (
            isinstance(data, dict)
            and data.get("ret_code") == 0
            and data.get("ret_context") == ""
            and data.get("ret_msg", "") == ""
        )

    def _is_status_response(self, data):
        if not isinstance(data, dict):
            return False
        ctx = data.get("ret_context")
        return isinstance(ctx, dict) and "motion_msg" in ctx

    def _parse_json(self, raw_bytes):
        """解析JSON响应，处理嵌套的ret_context字段"""
        if not raw_bytes: return None
        try:
            data = json.loads(raw_bytes.decode('utf-8'))
            if 'ret_context' in data and isinstance(data['ret_context'], str):
                try:
                    data['ret_context'] = json.loads(data['ret_context'])
                except:
                    pass
            return data
        except Exception as e:
            print(f"[解析JSON] 失败: {e}") 
            return None

    def _clear_data(self, input: Sequence[float], tolerance=1e-5) -> list[float]:
        """清除follower_cart模式下要传输位置接近0的值"""
        for i in range(len(input)):
            if abs(input[i]) < tolerance:
                input[i] = 0.0
        return input
    
    
if __name__ == "__main__":
    client = KaanhRobotBackend("192.168.1.10", DEFAULT_CONTROL_PORT)
    if client.connect():
        client.login("Engineer", "000000")
        time.sleep(0.5)
        client.manual_enable()
        print("已登录并使能机器人")

        client.set_jog_vel(100)
        client.set_pgm_vel(100)
        print("速度已设置为 100%")

        print("发送 MoveJ 指令...")
        joints = [0, -90, 90, 0, 90, 0]  # 目标关节角度
        vels = [50, 100, 50]            # 关节速度
        response = client.movej(joints, vels)
        print("MoveJ 指令完成，响应:")
        print(response)

        client.close()
