# 调用链：前端卡片 → server.py → 本 API → 商品动作函数 → 对应动作
"""HTTP adapter for the original keyboard movej action. No camera / YOLO required."""
import argparse
import copy
import hmac
import json
from logging import config
import os
from pathlib import Path
import threading
import time
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from robot.kaanh_backend import DEFAULT_CONTROL_PORT, KaanhRobotBackend
from commands.robot_commands import RobotCommandExecutor
from commands.setup import RobotSetup
# from commands.snapshot_pick_command import SnapshotPickCommand
from commands.snapshot import SnapShotCommand
from perception.roi_localizer import RoiPointCloudLocalizer
from robot.agv_backend import AGVBackend
from commands.hand_commands import GraspCommand
from planner.pose import calculate_pq_delta, quaternion_to_rotation

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"


class Runtime:
    # dry_run=True为无硬件演示，False为建立真实机器人连接。
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        # 先设为 None，便于退出时判断是否需要关闭机器人连接。
        self.robot = None
        # 如果确定控制机器人再加入依赖
        if not dry_run:
            # self.robot = KaanhRobotBackend(
            #     "192.168.100.99", DEFAULT_CONTROL_PORT, 9998, 10
            # )
            # if not self.robot.connect():
            #     raise RuntimeError('Robot connection failed')
            # 沿用原键盘控制线程的初始化顺序：登录 → 等待 → 手动使能 → 设置速度
            # 启动到这里就会使能设备，并非点击卡片后才使能
            # self.robot.login("Engineer", "000000")
            # time.sleep(.5)
            # self.robot.manual_enable()
            # # 初始程序设置
            # self.robot.set_pgm_vel(50)
            # self.robot.set_jog_vel(50)
            # self.robot.set_jog_coordinate()
            # self.agv = AGVBackend(
            #     ip="192.168.110.93",
            #     port=9201,
            #     device_id=1,
            #     timeout=3)
            # if not self.agv.connect():
            #     raise RuntimeError('AGV connection failed')
            # # 将已连接的后端交给原动作执行器，不复制或重写关节运动逻辑
            # self.robot_executor = RobotCommandExecutor(self.robot)

            self.second_photo_offset = [0, 0, 0]  # 第二次拍照的偏移量，单位 mm
            self.grasp_offset = [0, 0, 0]  # 抓取点的偏移量，单位 mm
            self.z_offset = [0, 0, 0]  # 往前移动准备抓取的偏移量，单位 mm

            setup = RobotSetup(DEFAULT_CONFIG_PATH)
            self.config = setup.get_robot_config()
            self.robot = setup.setup_robot(5999)
            self.camera = setup.setup_camera(0)
            self.detector = setup.setup_detector()
            self.localizer = RoiPointCloudLocalizer(
                self.config.localization,
                collect_inspection=True,
            )
            self.camera_transform = setup.setup_camera_transform()
            self.camera.start()
            time.sleep(self.config.camera_warmup_seconds)
            self.snapshot_executor = SnapShotCommand(
                robot=self.robot,
                camera=self.camera,
                detector=self.detector,
                localizer=self.localizer,
                camera_transform=self.camera_transform,
                tracker_config=self.config.tracker,
                show_yolo_result=True,
                show_point_cloud_result=True,
                is_save=True
            )
            self.robot_executor = RobotCommandExecutor(self.robot)
            # self.grasp_executor = GraspCommand(self.robot)
            self.agv = AGVBackend(
                ip="192.168.110.93",
                port=9201,
                device_id=1,
                timeout=3)

            if not self.robot.connect():
                raise RuntimeError('无法连接到机器人控制器')
            self.robot.login(
                str(self.config.robot.user), 
                str(self.config.robot.password))
            self.robot.set_jog_coordinate()
            self.robot.manual_enable()
            self.robot.set_pgm_vel(70)
            self.robot.set_jog_vel(70)

            # if not self.agv.connect(): 
            #     raise RuntimeError('无法连接到AGV控制器')
            
            self.snapshot_executor.initialize_resources()
            # self.grasp_executor.reinitialize()
            # self.grasp_executor.prepare()
            self.robot_executor.move_init_pose()
            print('机器人已连接，AGV已连接，摄像头已启动，动作执行器已初始化')
            


    # 只返回选中的方法
    # 新增物品时，在这里添加分支，并在下方添加对应 pick_xxx 方法
    def select_action(self, item_id):
        if item_id == 'water':
            return self.pick_water
        elif item_id == 'cola':
            return self.pick_cola
        elif item_id == 'oolong_tea':
            return self.pick_oolong_tea
        elif item_id == 'potato_chips':
            return self.pick_potato_chips
        elif item_id == 'cookies':
            return self.pick_cookies
        elif item_id == 'chocolate':
            return self.pick_chocolate
        else:
            # 未知ID时避免执行错误物品的动作
            raise ValueError('Unsupported item_id: ' + str(item_id))

    def execute(self, item_id):
        action = self.select_action(item_id)
        print('[Item dispatch]', item_id, '->', action.__name__, flush=True)
        return action()

    # 修改相应函数来执行对应动作
    def pick_water(self):
        # 矿泉水
        # self.snapshot_executor.pick("mineral_water")
        # self.agv.navigate_to(5)  # 导航到站点5，阻塞等待到站
        # self.robot_executor.move_place_pose()
        # self.robot_executor.move_arm_by_tool_offset(0,[15,0,150])
        # self.robot.hand_move(15,0,0,0,0,0,0,1000,1000)
        # self.robot_executor.move_transport_pose()
        # self.agv.navigate_to(4)  # 导航到站点5，阻塞等待到站
        # self.robot_executor.move_init_pose()

        #第一次拍照，判断有无物体，若有则计算第二处拍照点，随后移动到第二处拍照点，拍照，计算抓取点，抓取物体；若无则直接返回
        #高度（x）需要按照示教位做约束，从而确保抓取以及放置的稳定性
        rough_point = self.snapshot_executor.capture_once("mineral_water") 
        if rough_point is None:
            print("No target point found.")
            return 0

        print(rough_point.target_point_base_m)
        # tcp_pq = self.robot.get_robot_state().get_model(0).tcp_pq
        # tcp_pe = self.robot.get_robot_state().get_model(0).tcp_pe
        # photo_offset_in_base = quaternion_to_rotation(tcp_pq[3:]) @ self.second_photo_offset
        # photo_point_base = rough_point.target_point_base_m * 1000.0 + photo_offset_in_base
        # photo_point_base[0] = tcp_pq[0]  # 暂时保持机器人当前的 X 坐标（高度），仅使用偏置后的目标 Y、Z。
        #移动至机器人末端坐标系下的第二处拍照点，拍照，计算抓取点，抓取物体
        # photo_pe = np.concatenate((photo_point_base, tcp_pe[3:])) #mm, deg
        # self.robot.movel_model(0, photo_pe.tolist())
        time.sleep(5)  # 等待机械臂移动到位

        fine_point = self.snapshot_executor.capture_once("mineral_water")
        if fine_point is None:
            print("No target point found.")
            return 0
        print(fine_point.target_point_base_m)

        # observation = self.camera.get_latest_observation()
        # rgb_view = observation.rgb

        # tcp_pq = self.robot.get_robot_state().get_model(0).tcp_pq
        # tcp_pe = self.robot.get_robot_state().get_model(0).tcp_pe
        # grasp_offset_in_base = quaternion_to_rotation(tuple(tcp_pq[3:])) @ self.grasp_offset
        # grasp_point_base = fine_point.target_point_base_m * 1000.0 + grasp_offset_in_base
        # grasp_point_base[0] = tcp_pq[0] # 暂时保持机器人当前的 X 坐标（高度），仅使用偏置后的目标 Y、Z。
        # #移动至机器人末端坐标系下的抓取点.
        # grasp_pe = np.concatenate((grasp_point_base, tcp_pe[3:])) #mm, deg
        # print(f"[SingleShotExecutor] 移动到抓取位置: {grasp_pe}")
        # # self.robot.movel_model(0, grasp_pe.tolist())

        # tcp_pq = self.robot.get_robot_state().get_model(0).tcp_pq
        # tcp_pe = self.robot.get_robot_state().get_model(0).tcp_pe
        # z_offset_in_base = quaternion_to_rotation(tuple(tcp_pq[3:])) @ self.z_offset
        # target_point_base = grasp_point_base + z_offset_in_base
        # target_point_pe = np.concatenate((target_point_base * 1000.0, tcp_pe[3:])) #mm, deg
        # print(f"[SingleShotExecutor] 移动到抓取位置: {target_point_pe}")
        # # self.robot.movel_model(0, target_point_pe.tolist())


        return 0

    def pick_cola(self):
        # 可乐
        self.snapshot_executor.pick("coco_cola")
        self.agv.navigate_to(5)  # 导航到站点5，阻塞等待到站
        self.robot_executor.move_place_pose()
        self.robot_executor.move_arm_by_tool_offset(0,[15,0,150])
        self.robot.hand_move(15,0,0,0,0,0,0,1000,1000)
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(4)  # 导航到站点5，阻塞等待到站
        self.robot_executor.move_init_pose()
        return 0

    def pick_oolong_tea(self):
        # 乌龙茶
        self.snapshot_executor.pick("oolong_tea")
        self.agv.navigate_to(5)  # 导航到站点5，阻塞等待到站
        self.robot_executor.move_place_pose()
        self.robot_executor.move_arm_by_tool_offset(0,[15,0,150])
        self.robot.hand_move(15,0,0,0,0,0,0,1000,1000)
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(4)  # 导航到站点5，阻塞等待到站
        self.robot_executor.move_init_pose()
        return 0

    def pick_potato_chips(self):
        # 薯片
        print ("没有potato_chips动作")
        return 0

    def pick_cookies(self):
        # 饼干
        print ("没有cookies动作")
        return 0

    def pick_chocolate(self):
        # 巧克力
        print ("没有chocolate动作")
        return 0


# 前端server.py接收到completed后处理库存。
class Tasks:
    def __init__(self, runtime, path):
        self.runtime, self.path = runtime, Path(path)
        self.lock = threading.RLock()
        # 启动时恢复历史，以 task_id 为键，文件不存在时从空字典开始
        self.tasks = json.loads(self.path.read_text()) if self.path.exists() else {}
        for task in self.tasks.values():
            if task['status'] in ('queued','running'):
                task.update(status='needs_attention', error='API restarted; physical outcome unknown')
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 先写同目录临时文件，再替换正式JSON，降低正式文件只写入一部分的风险
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.tasks,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)

    # 接收一次卡片请求，key 为前端request_id，item为物品ID，例：water
    # request_id为机器人端task_id
    def submit(self, key, item):
        with self.lock:
            # 相同请求ID + 相同物品：返回旧任务
            # 相同请求ID + 不同物品：拒绝
            if key in self.tasks:
                if self.tasks[key]['item_id'] != item:
                    raise ValueError('request_id already used for another item')
                # 返回副本，避免调用方拿到共享字典后改变任务内容
                return copy.deepcopy(self.tasks[key])
            action = self.runtime.select_action(item)
            # 已有排队、执行中或待核对任务就拒绝新请求
            if any(t['status'] in ('queued','running','needs_attention') for t in self.tasks.values()):
                raise ValueError('robot_busy_or_uncertain')
            # 保存物品、选中的独立动作名和模式，便于检查ID到动作的对应关系
            task = dict(task_id=key,item_id=item,action=action.__name__,status='queued',simulation=self.runtime.dry_run)
            self.tasks[key] = task
            self.save()
            threading.Thread(target=self.run,args=(key,),daemon=True).start()
            return copy.deepcopy(task)

    # 后台执行单个任务，通过同一个 task_id 更新状态
    def run(self,key):
        with self.lock:
            self.tasks[key]['status']='running'
            self.save()
        print('[action running]',key,self.tasks[key]['item_id'],flush=True)
        try:
            # 等待控制器回复时不持有任务锁，HTTP 查询仍能读取running状态
            ack=self.runtime.execute(self.tasks[key]['item_id'])
            with self.lock:
                # 只有execute正常返回才完成，并保存原回复供排查和两端联调
                self.tasks[key].update(status='completed',controller_reply=ack)
        except Exception as e:
            with self.lock:
                # 保存错误原因，后续新任务会被submit拦截，不会自动重试动作
                self.tasks[key].update(status='needs_attention',error=str(e))
        with self.lock:
            self.save()
            print('[action result]',json.dumps(self.tasks[key],ensure_ascii=False),flush=True)


# 构造HTTP服务对象
# manager是任务管理器，token用于身份验证，host/port是HTTP地址和端口
def serve(manager, token, host, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            status_code = 0
            if len(args) > 1:
                try:
                    status_code = int(args[1])
                except (TypeError, ValueError):
                    pass

            if self.command == 'POST' or status_code >= 400:
                super().log_message(format, *args)

        def send(self,code,data):
            body=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                self.send(401,{'error':'unauthorized'});return False
            return True
        def do_GET(self):
            if not self.authorized(): return
            with manager.lock:
                if self.path=='/api/v1/health':
                    return self.send(200,{'ready':not any(t['status']=='needs_attention' for t in manager.tasks.values()),'action':'movej','simulation':manager.runtime.dry_run})
                # 返回历史任务列表
                if self.path=='/api/v1/tasks': return self.send(200,{'tasks':list(manager.tasks.values())})
                # 通常请求 /api/v1/tasks/<task_id>；找到返回 200，否则返回 404。
                task=manager.tasks.get(self.path.removeprefix('/api/v1/tasks/'))
                self.send(200 if task else 404, task or {'error':'unknown task'})
        # 创建动作任务：POST /api/v1/picks
        # 请求示例：{"request_id":"xxx","item_id":"water"}
        def do_POST(self):
            if not self.authorized(): return
            if self.path!='/api/v1/picks': return self.send(404,{'error':'unknown endpoint'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096: raise ValueError('invalid body size')
                body=json.loads(self.rfile.read(size))
                key=body.get('request_id');item=body.get('item_id')
                # 这里验证字段格式，submit会校验是否存在对应动作，库存检查由前端服务负责
                if not all(isinstance(x,str) and 0<len(x)<=128 for x in (key,item)): raise ValueError('item_id/request_id required')
                self.send(202,manager.submit(key,item))
            except (ValueError,TypeError,AttributeError) as e:
                self.send(409,{'error':str(e)})
    return ThreadingHTTPServer((host,port),Handler)

# 主程序
if __name__=='__main__':
    # 示例：python run_movej_api.py --dry-run
    # 真实模式：python run_movej_api.py
    p=argparse.ArgumentParser()
    # --dry-run：无硬件演示；--host：默认监听所有本机网卡；--port：HTTP 默认 8088
    p.add_argument('--dry-run',action='store_true');p.add_argument('--host',default='0.0.0.0');p.add_argument('--port',type=int,default=8088)
    # 机器人端任务文件默认位于本脚本旁的data/movej_tasks.json，与前端库存分开
    p.add_argument('--tasks-data',default=str(Path(__file__).parent/'data/movej_tasks.json'))
    # 启动前设置环境变量ROBOT_API_TOKEN，前端的 KAANH_API_TOKEN 必须与它一致
    a=p.parse_args();token=os.environ.get('ROBOT_API_TOKEN','')
    if not token: p.error('Set ROBOT_API_TOKEN')
    # 初始化机器人运行层，真实模式在这里连接、登录和使能机器人。
    runtime=Runtime(a.dry_run)
    # 恢复任务历史，开始前先处理未结束的历史任务。
    http=serve(Tasks(runtime,a.tasks_data),token,a.host,a.port)
    print(f'agent API http://{a.host}:{a.port} dry_run={a.dry_run}',flush=True)
    try: http.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        http.server_close()
        if runtime.robot: runtime.robot.close()
