"""终端选择相机，显示最新 RGBFrame 快照；不连接机器人或 AGV。

运行：python rgb_frame_preview.py
先关闭占用相机的后端/Viewer。输入 1=D435、2=Orbbec，Ctrl+C 退出。
"""
from pathlib import Path
import queue
import threading
import time

import cv2
import numpy as np

from commands.setup import RobotSetup


CONFIG_DIR = Path(__file__).resolve().parent / 'config'
CAMERAS = {
    '1': ('head', 'D435', 'realsense_d435'),
    '2': ('left', 'Orbbec G305', 'orbbec_g305'),
}


def prompt_choice() -> str:
    """后台等待终端输入，主线程继续处理 OpenCV 窗口事件。"""
    answers = queue.Queue(maxsize=1)

    def read_input():
        try:
            answers.put(input('\n选择相机 [1=D435 / 2=Orbbec，Ctrl+C退出]：').strip())
        except EOFError as error:
            answers.put(error)

    threading.Thread(target=read_input, daemon=True).start()
    while True:
        cv2.waitKey(1)
        try:
            answer = answers.get(timeout=0.03)
        except queue.Empty:
            continue
        if isinstance(answer, EOFError):
            raise answer
        return answer


def main() -> None:
    cameras = {}
    print('RGBFrame 快照测试：首次选择时启动相机，之后复用同一个实例。')
    print('请先关闭占用相机的后端和 Viewer；本脚本不发送机器人或 AGV 指令。')
    try:
        setup = RobotSetup(CONFIG_DIR)
        while True:
            choice = prompt_choice()
            if choice not in CAMERAS:
                print('请输入 1 或 2。')
                continue
            name, label, expected_type = CAMERAS[choice]
            try:
                if name not in cameras:
                    if setup.get_camera_settings(name).camera_type != expected_type:
                        raise ValueError(f'config/camera/cameras.json 的 {name} 应配置为 {expected_type}')
                    camera = setup.setup_camera(name)
                    # 先登记，再启动；启动失败或 Ctrl+C 时仍能释放资源。
                    cameras[name] = camera
                    print(f'正在启动 {label}…', flush=True)
                    camera.start()
                camera = cameras[name]
                frame = camera.get_latest_color_frame()
                deadline = time.monotonic() + 2.0
                while frame is None and time.monotonic() < deadline:
                    cv2.waitKey(1)
                    time.sleep(0.02)
                    frame = camera.get_latest_color_frame()
                if frame is None:
                    print(f'{label} 暂无 RGBFrame，可能正在恢复，请稍后重新选择。')
                    continue
                # RGBFrame 为 RGB；OpenCV imshow 使用 BGR。不修改只读源数组。
                bgr = np.ascontiguousarray(frame.rgb[..., ::-1])
                cv2.imshow(f'RGBFrame - {label}', bgr)
                cv2.waitKey(1)
                height, width = frame.rgb.shape[:2]
                print(f'{label}: frame_id={frame.frame_id}, '
                      f'timestamp_ms={frame.capture_timestamp_ms}, {width}x{height}')
            except Exception as error:
                print(f'{label} 读取失败：{error}')
                camera = cameras.get(name)
                if camera is not None:
                    try:
                        camera.close()
                        cameras.pop(name)
                    except Exception as close_error:
                        print(f'{label} 资源释放失败：{close_error}')
    except (KeyboardInterrupt, EOFError):
        print('\n正在退出…')
    finally:
        for name, camera in cameras.items():
            try:
                camera.close()
            except Exception as error:
                print(f'{name} 资源释放失败：{error}')
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
