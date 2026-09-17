from robot.agv_backend import AGVBackend

agv = AGVBackend(
    ip="192.168.110.93",
    port=9201,
    device_id=1,
    timeout=3
)

agv.connect()
agv.navigate_to(4)