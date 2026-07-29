import socket
import struct
import json

class FollowerUdpClient:
    """UDP client for ARIS follower_cart mode."""

    def __init__(self, ip, port=9998, timeout=1.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.is_connected = False

    def connect(self):
        """Create the UDP socket and bind it to the controller endpoint."""
        self.close()
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.ip, self.port))
            self.is_connected = True
            print(f"[Follower UDP] connected: {self.ip}:{self.port}")
            return True
        except Exception as exc:
            print(f"[Follower UDP] connect failed: {exc}")
            self.close()
            return False

    def close(self):
        """Close the UDP socket without affecting the WebSocket client."""
        self.is_connected = False
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None


    def send_pose_quaternion(
        self,
        x=0.0,
        y=0.0,
        z=0.0,
        qx=0.0,
        qy=0.0,
        qz=0.0,
        qw=1.0,
    ):
        """发送 pq 位姿增量，位置单位为米，四元数顺序为 qx,qy,qz,qw。"""
        if not self.is_connected or self.socket is None:
            return False

        message = {
            "type": "pq",
            "pq": [[x, y, z, qx, qy, qz, qw]],
        }

        payload = json.dumps(message).encode("utf-8")
        header = struct.pack(
            "<IIQqqq",
            len(payload), 0, 0, 0, 0, 0
        )

        try:
            self.socket.send(header + payload)
            return True
        except OSError as exc:
            print(f"[Follower UDP] send failed: {exc}")
            self.is_connected = False
            return False
