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
from commands.hand_commands import HandCommandExecutor
from workflows.two_stage_pick_workflow import TwoStagePickWorkflow

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"
# 测试任务中的相机画面时，暂时关闭新增的实机库存识别检查。
CHECK_INVENTORY_BEFORE_PICK = False
ITEM_TARGETS = {
    'water': 'mineral_water', 'cola': 'coco_cola', 'oolong_tea': 'oolong_tea',
    'potato_chips': 'potato_chips', 'cookies': 'cookies', 'chocolate': 'chocolate',
}


class OutOfStockError(ValueError):
    """识别正常完成，但没有找到所选商品。"""


class Runtime:
    # dry_run=True为无硬件演示，False为建立真实机器人连接。
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._jpeg_lock = threading.Lock()
        self._jpeg_frame = None
        self._jpeg_data = None
        # 先设为 None，便于退出时判断是否需要关闭机器人连接。
        self.robot = None
        # 如果确定控制机器人再加入依赖
        if not dry_run:
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
                show_yolo_result=False,
                show_point_cloud_result=False,
                is_save=True
            )
            self.robot_executor = RobotCommandExecutor(self.robot)
            self.hand_executor = HandCommandExecutor(self.robot)
            self.agv = AGVBackend(
                ip="192.168.110.93",
                port=9201,
                device_id=1,
                timeout=3)
            
            self.two_stage_pick_workflow = TwoStagePickWorkflow(
                robot=self.robot,
                robot_executor=self.robot_executor,
                hand_executor=self.hand_executor,
                snapshot_command=self.snapshot_executor
            )

            if not self.robot.connect():
                raise RuntimeError('无法连接到机器人控制器')
            self.robot.login(
                str(self.config.robot.user), 
                str(self.config.robot.password))
            self.robot.set_jog_coordinate()
            self.robot.manual_enable()
            self.robot.set_pgm_vel(70)
            self.robot.set_jog_vel(70)

            if not self.agv.connect(): 
                raise RuntimeError('无法连接到AGV控制器')
            self.snapshot_executor.initialize_resources()
            self.hand_executor.reinitialize(15)
            self.hand_executor.prepare(15)
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
        if self.dry_run:
            return 0
        return action()

    def check_inventory(self, item_ids):
        # 只检查当前画面中是否有目标，不统计数量，也不控制机器人运动。
        observation = self.camera.get_latest_observation()
        if observation is None:
            raise RuntimeError('相机暂时没有可用画面')
        return {
            item: int(bool(self.detector.detect(observation, ITEM_TARGETS[item]).detections))
            if ITEM_TARGETS[item] in self.detector.target_ids else 0
            for item in item_ids
        }

    def head_camera_jpeg(self):
        if self.dry_run:
            raise RuntimeError('模拟模式没有真实相机画面')
        # 相机发布的是只读观测；预览不调用 YOLO，也不等待动作线程。
        with self._jpeg_lock:
            observation = self.camera.get_latest_observation()
            if observation is None:
                raise RuntimeError('相机暂时没有可用画面')
            frame = (observation.frame_id, observation.capture_timestamp_ms)
            if frame != self._jpeg_frame:
                import cv2
                ok, encoded = cv2.imencode('.jpg', np.ascontiguousarray(observation.rgb[..., ::-1]))
                if not ok:
                    raise RuntimeError('相机画面编码失败')
                self._jpeg_data = encoded.tobytes()
                self._jpeg_frame = frame
            return self._jpeg_data

    # 修改相应函数来执行对应动作
    def pick_water(self):
        # 矿泉水
        ret = self.two_stage_pick_workflow.execute(0, "mineral_water")
        if ret != 0:
            print(f"[抓取] 矿泉水抓取失败，返回码 {ret}。")
            return 0
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(5)
        self.robot_executor.move_place_pose()
        self.robot_executor.move_arm_by_tool_offset(0,[35.5,0,0])
        self.hand_executor.release(15)
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(4)
        self.robot_executor.move_init_pose()

        return 0

    def pick_cola(self):
        # 可乐
        ret = self.two_stage_pick_workflow.execute(0, "coco_cola")
        if ret != 0:
            print(f"[抓取] 可乐抓取失败，返回码 {ret}。")
            return 0
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(5)
        self.robot_executor.move_place_pose()
        self.robot_executor.move_arm_by_tool_offset(0,[35.5,0,0])
        self.hand_executor.release(15)
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(4)
        self.robot_executor.move_init_pose()
        return 0

    def pick_oolong_tea(self):
        # 乌龙茶
        ret = self.two_stage_pick_workflow.execute(0, "oolong_tea")
        if ret != 0:
            print(f"[抓取] 乌龙茶抓取失败，返回码 {ret}。")
            return 0
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(5)
        self.robot_executor.move_place_pose()
        self.robot_executor.move_arm_by_tool_offset(0,[35.5,0,0])
        self.hand_executor.release(15)
        self.robot_executor.move_transport_pose()
        self.agv.navigate_to(4)
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


# 启动时默认有库存；仅点击商品后识别更新，不在任务完成后扣减数量。
class Tasks:
    def __init__(self, runtime, path):
        self.runtime, self.path = runtime, Path(path)
        self.lock = threading.RLock()
        self.device_lock = threading.Lock()
        self.stock = {item: 1 for item in ITEM_TARGETS}
        self.stock_revision = 0
        self.confirmation = {}
        # 启动时恢复历史，以 task_id 为键，文件不存在时从空字典开始
        self.tasks = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        for task in self.tasks.values():
            if task['status'] in ('queued','running'):
                task.update(status='failed', error='API restarted; physical outcome unknown', error_code='api_restarted')
            elif task['status'] in ('needs_attention','need_attention','need_aatention'):
                task.update(status='failed')
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 先写同目录临时文件，再替换正式JSON，降低正式文件只写入一部分的风险
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.tasks,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(self.path)

    def update_stock(self, values):
        with self.lock:
            if any(self.stock[item] != value for item, value in values.items()):
                self.stock.update(values)
                self.stock_revision += 1

    def inventory(self):
        with self.lock:
            # 页面轮询只读取库存记录，不拍照、不调用 YOLO。
            return {'dry_run': self.runtime.dry_run, 'revision': self.stock_revision,
                    'items': dict(self.stock)}

    def set_test_inventory(self, body):
        with self.lock:
            if not self.runtime.dry_run:
                raise PermissionError('仅模拟模式允许修改测试库存')
            if any(task['status'] in ('queued','running') for task in self.tasks.values()):
                raise ValueError('robot_busy')
            item, value = body.get('item_id'), body.get('available')
            confirmation = body.get('confirmation_available', value)
            if not isinstance(item, str) or item not in ITEM_TARGETS:
                raise ValueError('未知商品')
            if any(type(n) is not int or n not in (0,1) for n in (value, confirmation)):
                raise ValueError('库存只能为整数 0 或 1')
            self.update_stock({item: value})
            self.confirmation[item] = confirmation
            return self.inventory()

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
            # 失败是终态，不再阻塞后续任务；执行中仍禁止并发动作。
            if any(t['status'] in ('queued','running') for t in self.tasks.values()):
                raise ValueError('robot_busy')
            # 保存物品、选中的独立动作名和模式，便于检查ID到动作的对应关系
            task = dict(task_id=key,item_id=item,action=action.__name__,status='queued',phase='checking',simulation=self.runtime.dry_run)
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
            item = self.tasks[key]['item_id']
            # 库存检查可暂时关闭；商品任务仍按原来的动作流程执行。
            with self.device_lock:
                if self.runtime.dry_run:
                    available = self.confirmation.get(item, self.stock[item])
                elif CHECK_INVENTORY_BEFORE_PICK:
                    available = self.runtime.check_inventory((item,))[item]
                else:
                    available = 1
                self.update_stock({item: available})
                if available == 0:
                    raise OutOfStockError('该商品已售尽')
                with self.lock:
                    self.tasks[key]['phase'] = 'picking'
                # 等待控制器回复时不持有任务锁，HTTP 查询仍能读取running状态
                ack=self.runtime.execute(self.tasks[key]['item_id'])
            with self.lock:
                # 只有execute正常返回才完成，并保存原回复供排查和两端联调
                self.tasks[key].update(status='completed',phase='placed',controller_reply=ack)
        except OutOfStockError as e:
            with self.lock:
                if self.runtime.dry_run or CHECK_INVENTORY_BEFORE_PICK:
                    self.update_stock({self.tasks[key]['item_id']: 0})
                    self.tasks[key].update(status='failed',phase='checking',error=str(e),error_code='out_of_stock')
                else:
                    # 动作内部的识别失败只结束任务，不把商品卡片锁成已售尽。
                    self.tasks[key].update(status='failed',error=str(e),error_code='execution_failed')
        except Exception as e:
            with self.lock:
                # 保存错误原因并结束本次任务，不自动重试动作。
                self.tasks[key].update(status='failed',error=str(e),error_code='execution_failed')
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
            if self.path=='/api/v1/cameras/head/frame.jpg':
                try:
                    raw = manager.runtime.head_camera_jpeg()
                except Exception as e:
                    return self.send(503,{'error':str(e),'error_code':'camera_unavailable'})
                try:
                    self.send_response(200)
                    self.send_header('Content-Type','image/jpeg')
                    self.send_header('Content-Length',str(len(raw)))
                    self.send_header('Cache-Control','no-store')
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            if self.path=='/api/v1/inventory':
                try:
                    return self.send(200,manager.inventory())
                except Exception as e:
                    return self.send(503,{'error':str(e),'error_code':'inventory_unavailable'})
            with manager.lock:
                if self.path=='/api/v1/health':
                    return self.send(200,{'ready':not any(t['status'] in ('queued','running') for t in manager.tasks.values()),'action':'movej','simulation':manager.runtime.dry_run})
                # 返回历史任务列表
                if self.path=='/api/v1/tasks': return self.send(200,{'tasks':list(manager.tasks.values())})
                # 通常请求 /api/v1/tasks/<task_id>；找到返回 200，否则返回 404。
                task=manager.tasks.get(self.path.removeprefix('/api/v1/tasks/'))
                self.send(200 if task else 404, task or {'error':'unknown task'})
        # 创建动作任务：POST /api/v1/picks
        # 请求示例：{"request_id":"xxx","item_id":"water"}
        def do_POST(self):
            if not self.authorized(): return
            if self.path not in ('/api/v1/picks','/api/v1/test/inventory'):
                return self.send(404,{'error':'unknown endpoint'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096: raise ValueError('invalid body size')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict): raise ValueError('JSON object required')
                if self.path=='/api/v1/test/inventory':
                    return self.send(200,manager.set_test_inventory(body))
                key=body.get('request_id');item=body.get('item_id')
                # 校验请求字段；后台任务在执行动作前重新识别并确认库存。
                if not all(isinstance(x,str) and 0<len(x)<=128 for x in (key,item)): raise ValueError('item_id/request_id required')
                self.send(202,manager.submit(key,item))
            except PermissionError as e:
                self.send(403,{'error':str(e)})
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
