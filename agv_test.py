from robot.agv_backend import AGVBackend

def agv_test():
    agv = AGVBackend(
        ip="192.168.110.93",
        port=9201,
        device_id=1,
        timeout=3
        )

    if not agv.connect():
        print("Failed to connect to AGV")
        return 0
    result = agv.navigate_to(4)  # 导航到站点1，阻塞等待到站
    print(result.success)
    print(result.message)
    return 0


if __name__ == "__main__":
    agv_test()