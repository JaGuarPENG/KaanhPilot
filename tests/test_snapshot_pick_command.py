import unittest

from commands.snapshot_pick_command import SnapshotPickCommand


class _FakeDetector:
    target_ids = ("oolong_tea", "cola")


class _FakeRobot:
    def __init__(self, events):
        self._events = events
        self.close_calls = 0

    def hand_en(self, hand_id):
        self._events.append(("hand_en", hand_id))

    def hand_move(self, **parameters):
        self._events.append(("hand_move", parameters))

    def close(self):
        self.close_calls += 1


class _FakeCamera:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _FakeSingleShot:
    def __init__(self, events):
        self._events = events

    def execute(self, target_id):
        self._events.append(("single_shot", target_id))


class _FakeRobotExecutor:
    def __init__(self, events):
        self._events = events

    def move_arm_by_tool_offset(self, model_id, offset_mm):
        self._events.append(("retreat", model_id, tuple(offset_mm)))


class SnapshotPickCommandTest(unittest.TestCase):
    def _initialized_command(self):
        events = []
        robot = _FakeRobot(events)
        command = SnapshotPickCommand(robot, hold_seconds=0.0)
        command._camera = _FakeCamera()
        command._detector = _FakeDetector()
        command._single_shot = _FakeSingleShot(events)
        command._robot_executor = _FakeRobotExecutor(events)
        command._initialized = True
        return command, events

    def test_pick_executes_the_complete_snapshot_pick_sequence(self):
        command, events = self._initialized_command()

        command.pick("oolong_tea")

        self.assertEqual(events[0], ("hand_en", 15))
        self.assertEqual(events[1][0], "hand_move")
        self.assertEqual(events[2], ("single_shot", "oolong_tea"))
        self.assertEqual(events[3][0], "hand_move")
        self.assertEqual(events[4][0], "hand_move")
        self.assertEqual(events[5], ("retreat", 0, (-50.0, 0.0, -100.0)))

        # 第一次和第三次 hand_move 是张手，第二次是闭合抓取。
        self.assertEqual(events[1][1]["j2"], 0)
        self.assertEqual(events[3][1]["j2"], 5800)
        self.assertEqual(events[4][1]["j2"], 0)

    def test_pick_requires_initialized_resources(self):
        command = SnapshotPickCommand(_FakeRobot([]))

        with self.assertRaisesRegex(RuntimeError, "initialize_resources"):
            command.pick("oolong_tea")

    def test_pick_rejects_target_not_supported_by_detector(self):
        command, events = self._initialized_command()

        with self.assertRaisesRegex(ValueError, "不支持目标"):
            command.pick("unknown")

        self.assertEqual(events, [])

    def test_close_releases_camera_but_keeps_external_robot_open(self):
        command, _ = self._initialized_command()
        robot = command._robot
        camera = command._camera

        command.close()

        self.assertEqual(camera.close_calls, 1)
        self.assertEqual(robot.close_calls, 0)
        self.assertIs(command._robot, robot)
        self.assertFalse(command.is_initialized)


if __name__ == "__main__":
    unittest.main()
