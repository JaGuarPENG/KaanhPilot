from typing import Sequence
import websocket
import struct
import json
import hashlib
import time

from robot.follower_udp_client import FollowerUdpClient
from robot.robot_state import RobotState, parse_robot_state

class KaanhRobotBackend:
    """Kaanh 机器人后端客户端
    
    输入参数表：
    - ip: 机器人 IP 地址
    - port: WebSocket 控制端口 (默认 5999)
    - udp_port: UDP 端口 (默认 9998)
    - timeout: 连接超时时间 (默认 10 秒)
    
    """


    def __init__(self, ip, port=5999, udp_port=9998, timeout=10):
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


    def movej(self, joints, vels):
        """发送关节运动指令"""
        try:
            if len(joints) != 6:
                print(f"[错误] movej 需要 6 个关节角度")
                return None
            
            joint_strs = [f"DOUBLE{{{j:.6f}}}" for j in joints]
            joint_inner_str = ",".join(joint_strs)

            vel_strs = [f"DOUBLE{{{v:.6f}}}" for v in vels]
            vel_inner_str = ",".join(vel_strs)
  
            # 【修改】注意 Speed 后面加了三层花括号 {{{...}}} 
            # f-string中 {{ 转义为 {，所以 {{{ }}} 解析为 {内容}
            cmd = f"manual_mvaj --pos=JointTarget{{UrModel_JointTarget{{{joint_inner_str}}}}} --vel=Speed{{{vel_inner_str}}}"
            
            # 这里的 response 会一直阻塞直到机器人动作完成并返回
            response = self._send_raw_command(cmd)
            return response
            
        except Exception as e:
            print(f"[MoveJ] 执行出错: {e}")
            return None
        
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

    def _pack_header(self, msg_len):
        return struct.pack('<IIQqqq', msg_len, 0x01, 0x1000, 0xA1B2C3D4, 0, 0)

    def _send_raw_command(self, cmd_str, need_reply=True, need_check=True):
        """发送原始指令，返回原始响应 (字节)"""
        if not self.is_connected: 
            raise RuntimeError("WebSocket未连接，无法发送指令")
        if need_check:
            self.get_robot_state()  # 每次发送指令前获取最新状态，更新 error_code 和 robot_status
            if self.error_code not in (None, 0):
                raise RuntimeError(f"机器人存在错误，错误码={self.error_code}，状态={self.robot_status}")
            self._drain_empty_acks()  # 清空可能存在的空ACK，避免干扰后续指令
        try:
            payload = cmd_str.encode('utf-8')
            full_packet = self._pack_header(len(payload)) + payload
            self.ws.send(full_packet, opcode=websocket.ABNF.OPCODE_BINARY)
            if not need_reply:
                return None
            # 阻塞等待接收
            return self._recv_payload()
        except websocket.WebSocketTimeoutException:
            print(f"[超时] 指令发送成功，但在 {self.timeout}秒内未收到回复 (可能动作时间过长)")
            return None
        except Exception as e:
            print(f"[异常] {e}")
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
    client = KaanhRobotBackend("192.168.1.10", 5888)
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
