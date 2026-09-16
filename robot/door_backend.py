# 使用网络继电器控制会议室大门的后端程序
# 目前继电器配置的是client模式，监听服务器（本机）的6000端口，接收到AT+STACH2=1\r\n指令时吸合继电器，接收到AT+STACH2=0\r\n指令时释放继电器。
# 后续改进：继电器改成server模式，让本程序主动连接继电器，发送指令控制继电器吸合和释放。
# 需要将功能模块完善成一个完整的后端程序，提供API接口供前端调用，实现远程控制大门的功能。
# 使用时会议室的大门必须配置为自动模式！！

import socket
import time


with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.bind(("0.0.0.0", 6000))
    server.listen(1)
    print("等待继电器连接...")

    relay, address = server.accept()
    with relay:
        print(f"继电器已连接：{address}")
        relay.sendall(b"AT+STACH2=1\r\n")
        print("OUT2 已吸合")

        time.sleep(3)

        relay.sendall(b"AT+STACH2=0\r\n")
        print("OUT2 已释放")
