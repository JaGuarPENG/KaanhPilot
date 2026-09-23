import time
import threading
import queue
import numpy as np
import roboticstoolbox as rtb
import matplotlib.pyplot as plt

# 导入模块 (确保路径正确)
from robot.robot_dh import create_ka_ur, create_humaniod_robot
from robot.kaanh_backend import (
    DEFAULT_CONTROL_PORT,
    DEFAULT_MONITOR_PORT,
    KaanhRobotBackend,
)
from commands.robot_commands import RobotCommandExecutor
from commands.hand_commands import HandCommandExecutor
from robot.robot_state import RobotState

# 全局变量
shared_data = {
    "joints": None,     
    "running": True,
}
data_lock = threading.Lock() 
cmd_queue = queue.Queue()

# ROBOT_IP = "192.168.110.77"  # 请根据实际情况修改为机器人的IP地址

ROBOT_IP = "192.168.100.99"  # 请根据实际情况修改为机器人的IP地址
PORT_MONITOR = DEFAULT_MONITOR_PORT  # 监听端口
UDP_PORT = 9998  # UDP端口
PORT_CONTROL = DEFAULT_CONTROL_PORT  # 控制端口

send_freq = 125

# ==========================================================
# 线程 1: 监控线程 (连 5888) - 只管读，拼命读
# ==========================================================
def monitor_thread_func():
    print(f"[监控线程] 启动，连接 {ROBOT_IP}:{PORT_MONITOR}...")
    # 超时设短 (3s)，保证实时性
    monitor = KaanhRobotBackend(ROBOT_IP, PORT_MONITOR, timeout=3)
    
    # RBT j1..j20 从真实机器人关节向量中取值的下标：
    # 腰部（base 向上） <- 真实电机 15..18；
    # 头部 <- 真实电机 19,20；左臂 <- 真实电机 1..7；右臂 <- 真实电机 8..14。
    rtb_from_real = np.array([
        17, 16, 15, 14,  # RBT j1..j4  <- 真实 j15..j18（腰部）
        18, 19,          # RBT j5..j6  <- 真实 j19..j20（头部）
        0, 1, 2, 3, 4, 5, 6,  # RBT j7..j13 <- 真实 j1..j7（左臂）
        7, 8, 9, 10, 11, 12, 13,  # RBT j14..j20 <- 真实 j8..j14（右臂）
    ])
    
    if not monitor.connect():
        print("[监控线程] 连接失败 (请检查5888端口)")
        return

    try:
        monitor.login("Engineer", "000000")
        print("[监控线程] 准备就绪，开始实时刷新...")
        
        while shared_data["running"]:
            # 发送 get，永不阻塞
            joint_data = monitor.get_robot_state().joints_deg   
            
            joint_rtb = np.asarray(joint_data, dtype=float)[rtb_from_real]
            with data_lock:
                shared_data["joints"] = joint_rtb

            # 50Hz 高频刷新
            time.sleep(0.02)

    except Exception as e:
        print(f"[监控线程] 异常: {e}")
    finally:
        monitor.close()
        print("[监控线程] 退出")

# ==========================================================
# 线程 2: 控制线程 (连 5999) - 只管发，慢慢等
# ==========================================================
def control_thread_func():
    print(f"[控制线程] 启动，连接 {ROBOT_IP}:{PORT_CONTROL}...")
    # 超时设长 (60s)，允许长动作
    robot = KaanhRobotBackend(ROBOT_IP, PORT_CONTROL, UDP_PORT, timeout=60)
    if not robot.connect():
        print("[控制线程] 连接失败 (请检查5999端口是否已开启！)")
        return

    try:
        robot.login("Engineer", "000000")
        time.sleep(0.5)
        robot.manual_enable()
        print("[控制线程] 准备就绪，等待按键指令...")
        robot.set_pgm_vel(70)  # 设置程序速度为 70%
        robot.set_jog_vel(70)  # 设置JOG速度为 70%
        print("[控制线程] 速度设置为 70%")
        robot.set_jog_coordinate()  # 设置JOG坐标系为工具
        print("[控制线程] JOG坐标系设置为工具")
        executor = RobotCommandExecutor(robot)
        grasp_executor = HandCommandExecutor(robot)

        while shared_data["running"]:
            try:
                # 阻塞等待队列指令
                cmd = cmd_queue.get(timeout=1.0)
                if cmd['type'] == 'movej':
                    print(f">>> [开始] MoveJ 到初始位...")
                    # 这里的阻塞只会卡住这个线程，完全不影响监控线程的画面
                    executor.move_init_pose() 
                    print(f"<<< [结束] MoveJ 完成.")

                elif cmd['type'] == 'robot_enable':
                    print(f">>> [开始] 机器人使能...")
                    robot.manual_enable()
                    print(f"<<< [结束] 机器人使能完成.")

                elif cmd['type'] == 'hand_enable':
                    print(f">>> [开始] 灵巧手重置...")
                    grasp_executor.reinitialize(15)
                    print(f"<<< [结束] 灵巧手重置完成.")

                elif cmd['type'] == 'hand_prepare':
                    print(f">>> [开始] 灵巧手预备位...")
                    grasp_executor.prepare(15)
                    print(f"<<< [结束] 灵巧手预备位完成.")
                
                elif cmd['type'] == 'hand_grasp':
                    print(f">>> [开始] 灵巧手抓取...")
                    grasp_executor.grasp(15)
                    print(f"<<< [结束] 灵巧手抓取完成.")

                elif cmd['type'] == 'hand_release':
                    print(f">>> [开始] 灵巧手松开...")
                    grasp_executor.release(15)
                    print(f"<<< [结束] 灵巧手松开完成.")

                elif cmd['type'] == 'transport_pose':
                    print(f">>> [开始] 运输位...")
                    executor.move_transport_pose()
                    print(f"<<< [结束] 运输位完成.")

                elif cmd['type'] == 'place_pose':
                    print(f">>> [开始] 灵巧手放置...")
                    executor.move_place_pose()
                    print(f"<<< [结束] 灵巧手放置完成.")

                elif cmd['type'] == 'placeing':
                    print(f">>> [开始] 灵巧手放置中...")
                    executor.move_arm_by_tool_offset(0,[35.5,0,0])
                    # executor.movel_one_arm()
                    print(f"<<< [结束] 灵巧手后退完成.")

                # elif cmd['type'] == 'backward':
                #     print(f">>> [开始] 灵巧手后退...")
                #     executor.move_arm_by_tool_offset(0,[10,0,-80])
                #     print(f"<<< [结束] 灵巧手后退完成.")
            
                elif cmd['type'] == 'exit':
                    print("[控制线程] 收到退出指令，结束控制线程.")
                    break

                cmd_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[控制线程] 指令错误: {e}")

    except Exception as e:
        print(f"[控制线程] 异常: {e}")
    finally:
        robot.close()
        print("[控制线程] 退出")

# ==========================================================
# 键盘交互
# ==========================================================
def on_key_press(event):
    if event.key == '1':
        print("\n[按键] 1 -> 回初始位")
        cmd_queue.put({'type': 'movej'})

    elif event.key == '2':
        print("\n[Key] 2 -> 机器人使能")
        cmd_queue.put({'type': 'robot_enable'})

    elif event.key == '3':
        print("\n[Key] 3 -> 灵巧手使能")
        cmd_queue.put({'type': 'hand_enable'})

    elif event.key == '4':
        print("\n[Key] 4 -> 灵巧手预备位")
        cmd_queue.put({'type': 'hand_prepare'})

    elif event.key == '5':
        print("\n[Key] 5 -> 灵巧手抓取")
        cmd_queue.put({'type': 'hand_grasp'})

    elif event.key == '6':
        print("\n[Key] 6 -> 灵巧手松开")
        cmd_queue.put({'type': 'hand_release'})

    elif event.key == '7':
        print("\n[Key] 7 -> 运输位置")
        cmd_queue.put({'type': 'transport_pose'})

    elif event.key == '8':
        print("\n[Key] 8 -> 放置位置")
        cmd_queue.put({'type': 'place_pose'})

    elif event.key == '9':
        print("\n[Key] 9 -> 放下")
        cmd_queue.put({'type': 'placeing'})

    elif event.key.lower() == 'q':
        print("\n[Key] Q -> 退出程序")
        shared_data["running"] = False
        cmd_queue.put({'type': 'exit'})


# ==========================================================
# 主程序
# ==========================================================
def main():
    # 1. 启动监控线程 (Port 5888)
    t_mon = threading.Thread(target=monitor_thread_func, daemon=True)
    t_mon.start()

    # 2. 启动控制线程 (Port 5999)
    # 给一点时间让上一个连接建立好
    time.sleep(1) 
    t_ctrl = threading.Thread(target=control_thread_func, daemon=True)
    t_ctrl.start()

    # 3. 初始化 3D 界面
    print("[主界面] 加载 3D 模型...")
    robot = create_humaniod_robot()
    
    env = rtb.backends.PyPlot.PyPlot()
    env.launch()
    env.add(robot)
    
    env.ax.set_xlim([-1.2, 1.2])
    env.ax.set_ylim([-1.4, 1.4])
    env.ax.set_zlim([0.0, 2.1])

    env.ax.figure.canvas.mpl_connect('key_press_event', on_key_press)

    print("\n" + "="*60)
    print("   双端口并发模式 (5888:Monitor, 5999:Control)")
    print("   [1] : 回初始位")
    print("   [2] : 机器人使能")
    print("   [3] : 灵巧手回零位")
    print("   [4] : 灵巧手预备位")
    print("   [5] : 灵巧手抓取")
    print("   [6] : 灵巧手松开")
    print("   [7] : 运输位置")
    print("   [8] : 放置位置")
    print("   [9] : 放下")
    print("   [Q] : 退出")
    print("="*60)

    # 4. 绘图循环
    try:
        while shared_data["running"]:
            if not env.ax.figure.canvas.manager.window: break

            current_joints = None
            with data_lock:
                # joints 是多元素 NumPy 数组时，不能直接作为 if 条件。
                # 这里需要判断的是“是否已经收到关节数据”。
                joints_deg = shared_data["joints"]
                if joints_deg is not None:
                    # 机器人返回角度，模型需要弧度。
                    current_joints = np.deg2rad(joints_deg)
                    # print(f"[主界面] 当前关节角度 (deg): {joints_deg}")
            
            # 全部为 0 的关节角也是有效数据，因此同样只判断是否为 None。
            if current_joints is not None:
                robot.q = current_joints
            
            env.step(0.05)
            
    except KeyboardInterrupt:
        pass
    finally:
        shared_data["running"] = False
        t_mon.join(timeout=1)
        t_ctrl.join(timeout=1)
        print("程序结束")

if __name__ == "__main__":
    main()
