"""官方 Orbbec 滤波链的无硬件单元测试。"""

import unittest

from camera.adapters.orbbec.filters import OrbbecDepthFilterChain
from camera.contracts.models import DepthProcessingConfig


class FakeDepthFrame:
    def as_depth_frame(self):
        return self


class FakeFilter:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def process(self, frame):
        self.calls.append(self.name)
        return frame

    def get_config_schema_vec(self):
        return []

    def set_config_value(self, name, value):
        if not hasattr(self, "config_values"):
            self.config_values = {}
        self.config_values[name] = value


class FakeThresholdFilter(FakeFilter):
    def __init__(self, calls):
        super().__init__("threshold", calls)
        self.value_range = None

    def set_value_range(self, minimum, maximum):
        self.value_range = (minimum, maximum)


class FakeOrbbecSdk:
    def __init__(self):
        self.calls = []
        self.threshold = None

    def TemporalFilter(self):
        return FakeFilter("temporal", self.calls)

    def SpatialAdvancedFilter(self):
        return FakeFilter("spatial", self.calls)

    def HoleFillingFilter(self):
        return FakeFilter("hole", self.calls)

    def ThresholdFilter(self):
        self.threshold = FakeThresholdFilter(self.calls)
        return self.threshold


class FakeSchema:
    name = "alpha"
    desc = "测试参数"
    type = type("ValueType", (), {"name": "FLOAT"})()
    default = 0.5
    min = 0.0
    max = 1.0
    step = 0.1


class OrbbecFilterChainTests(unittest.TestCase):
    def test_filters_run_in_official_order_and_threshold_uses_mm(self):
        sdk = FakeOrbbecSdk()
        config = DepthProcessingConfig(
            temporal_enabled=True,
            spatial_enabled=True,
            hole_filling_enabled=True,
            spatial_magnitude=3,
            spatial_alpha=0.7,
            hole_filling_mode=1,
            minimum_depth_m=0.15,
            maximum_depth_m=2.0,
        )
        chain = OrbbecDepthFilterChain(sdk, config)
        result = chain.process(FakeDepthFrame())
        self.assertIsInstance(result, FakeDepthFrame)
        self.assertEqual(sdk.calls, ["temporal", "spatial", "hole", "threshold"])
        self.assertEqual(sdk.threshold.value_range, (150, 2000))
        self.assertEqual(chain.active_filter_names, ("TemporalFilter", "SpatialAdvancedFilter", "HoleFillingFilter", "ThresholdFilter"))
        filters = dict(chain._filters)
        self.assertEqual(filters["SpatialAdvancedFilter"].config_values, {"magnitude": 3, "alpha": 0.7})
        self.assertEqual(filters["HoleFillingFilter"].config_values, {"hole_filling_mode": 1})

    def test_default_config_creates_no_filters(self):
        sdk = FakeOrbbecSdk()
        chain = OrbbecDepthFilterChain(sdk, DepthProcessingConfig())
        chain.process(FakeDepthFrame())
        self.assertEqual(sdk.calls, [])

    def test_schema_is_mapped_to_vendor_neutral_descriptor(self):
        sdk = FakeOrbbecSdk()
        chain = OrbbecDepthFilterChain(sdk, DepthProcessingConfig(temporal_enabled=True))
        chain._filters[0][1].get_config_schema_vec = lambda: [FakeSchema()]
        descriptor = chain.parameter_descriptors()[0]
        self.assertEqual(descriptor.filter_name, "TemporalFilter")
        self.assertEqual(descriptor.name, "alpha")
        self.assertEqual(descriptor.value_type, "FLOAT")
        self.assertEqual((descriptor.default_value, descriptor.minimum, descriptor.maximum, descriptor.step), (0.5, 0.0, 1.0, 0.1))


class DepthProcessingConfigTests(unittest.TestCase):
    def test_partial_depth_range_is_rejected(self):
        with self.assertRaises(ValueError):
            DepthProcessingConfig(minimum_depth_m=0.1)

    def test_exposed_filter_parameters_are_validated(self):
        with self.assertRaises(ValueError):
            DepthProcessingConfig(spatial_magnitude=6)
        with self.assertRaises(ValueError):
            DepthProcessingConfig(spatial_alpha=0.05)
        with self.assertRaises(ValueError):
            DepthProcessingConfig(hole_filling_mode=3)


if __name__ == "__main__":
    unittest.main()
