import unittest
from unittest.mock import Mock, patch
import numpy as np

import rgb_frame_preview as preview
from camera.contracts import RGBFrame


class RGBPreviewTests(unittest.TestCase):
    def test_selection_reuses_camera_and_only_reads_rgb(self):
        cameras = {name: Mock() for name in ('head', 'left')}
        for camera in cameras.values():
            camera.get_latest_color_frame.return_value = RGBFrame(
                1, 10, np.full((2, 2, 3), [10, 20, 30], np.uint8))
        setup = Mock()
        setup.setup_camera.side_effect = cameras.__getitem__
        setup.get_camera_settings.side_effect = lambda name: Mock(
            camera_type='realsense_d435' if name == 'head' else 'orbbec_g305')
        with patch.object(preview, 'RobotSetup', return_value=setup), \
             patch.object(preview, 'prompt_choice', side_effect=['bad', '1', '1', '2', KeyboardInterrupt]), \
             patch.object(preview.cv2, 'imshow') as show, \
             patch.object(preview.cv2, 'waitKey'), \
             patch.object(preview.cv2, 'destroyAllWindows'):
            preview.main()
        self.assertEqual(show.call_count, 3)
        np.testing.assert_array_equal(show.call_args.args[1][0, 0], [30, 20, 10])
        for camera in cameras.values():
            camera.start.assert_called_once()
            camera.close.assert_called_once()
            camera.get_latest_observation.assert_not_called()
        setup.setup_robot.assert_not_called()

    def test_start_failure_is_closed_and_prompt_can_continue(self):
        setup = Mock()
        setup.get_camera_settings.return_value.camera_type = 'realsense_d435'
        camera = setup.setup_camera.return_value
        camera.start.side_effect = RuntimeError('device missing')
        with patch.object(preview, 'RobotSetup', return_value=setup), \
             patch.object(preview, 'prompt_choice', side_effect=['1', EOFError]), \
             patch.object(preview.cv2, 'destroyAllWindows'):
            preview.main()
        camera.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
