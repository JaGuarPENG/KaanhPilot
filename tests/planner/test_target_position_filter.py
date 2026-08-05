"""目标位置滤波器的无硬件单元测试。"""

from __future__ import annotations

import unittest

from planner.follower_bridge import BridgeState, FollowerBridge, FollowerBridgeConfig
from planner.target_position_filter import TargetPositionFilter


class TargetPositionFilterTests(unittest.TestCase):
    def test_first_sample_seeds_then_ema_smooths(self) -> None:
        """首个样本不能产生人为延迟，后续样本按 alpha 平滑。"""
        target_filter = TargetPositionFilter(alpha=0.25, jump_threshold_m=1.0)
        self.assertEqual(target_filter.update((0.0, 0.0, 0.0)).position_m, (0.0, 0.0, 0.0))
        result = target_filter.update((0.4, 0.0, 0.0))
        self.assertTrue(result.accepted)
        self.assertEqual(result.position_m, (0.1, 0.0, 0.0))

    def test_large_jump_keeps_last_stable_target(self) -> None:
        """跳变被拒绝时，follower 仍能使用上一个平稳目标。"""
        target_filter = TargetPositionFilter(jump_threshold_m=0.05)
        target_filter.update((0.0, 0.0, 0.0))
        result = target_filter.update((0.06, 0.0, 0.0))
        self.assertFalse(result.accepted)
        self.assertEqual(result.position_m, (0.0, 0.0, 0.0))

    def test_reset_accepts_new_target_as_seed(self) -> None:
        """重新获得目标后不得受到旧会话跳变阈值限制。"""
        target_filter = TargetPositionFilter(jump_threshold_m=0.05)
        target_filter.update((0.0, 0.0, 0.0))
        target_filter.reset()
        self.assertEqual(target_filter.update((1.0, 0.0, 0.0)).position_m, (1.0, 0.0, 0.0))


class BridgeStateTests(unittest.TestCase):
    def test_camera_point_is_transformed_into_base_coordinates(self) -> None:
        bridge = FollowerBridge(
            object(),
            FollowerBridgeConfig(
                camera_origin_in_base_m=(1.0, 2.0, 3.0),
                camera_to_base_quaternion_xyzw=(0.0, 0.0, 0.7071067811865476, 0.7071067811865476),
            ),
        )

        self.assertTupleEqual(
            tuple(round(value, 9) for value in bridge._camera_to_base((1.0, 0.0, 0.0))),
            (1.0, 3.0, 3.0),
        )

    def test_default_debug_state_is_a_stopped_snapshot(self) -> None:
        bridge = FollowerBridge(object(), FollowerBridgeConfig((0.0, 0.0, 0.5), (-0.5, 0.5, -0.5, 0.5)))

        self.assertEqual(bridge.debug_state, BridgeState("stopped", None, None, None))

    def test_debug_update_maps_each_point_to_its_named_field(self) -> None:
        bridge = FollowerBridge(object(), FollowerBridgeConfig((0.0, 0.0, 0.5), (-0.5, 0.5, -0.5, 0.5)))
        bridge._set_debug((1.0, 2.0, 3.0), (1.1, 2.1, 3.1), (1.1, 2.1, 3.0), "running")

        self.assertEqual(
            bridge.debug_state,
            BridgeState("running", (1.0, 2.0, 3.0), (1.1, 2.1, 3.1), (1.1, 2.1, 3.0)),
        )


if __name__ == "__main__":
    unittest.main()
