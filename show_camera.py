"""用 OpenCV 预览 config/camera/cameras.json 中指定相机的彩色画面。"""

from pathlib import Path
import time

import cv2

from commands.setup import RobotSetup


CAMERA_NAME = "right"  # 可改为 "head" 或 "right"
CONFIG_DIR = Path(__file__).resolve().parent / "config"


def main() -> None:
    setup = RobotSetup(CONFIG_DIR)
    camera = setup.setup_camera(CAMERA_NAME)
    warmup_seconds = setup.get_camera_settings(CAMERA_NAME).warmup_seconds
    window_name = f"{CAMERA_NAME} camera"

    try:
        camera.start()
        time.sleep(warmup_seconds)
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        while True:
            frame = camera.get_latest_color_frame()
            if frame is not None:
                bgr = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR)
                cv2.imshow(window_name, bgr)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        try:
            camera.close()
        finally:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
