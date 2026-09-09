import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from robot.agv_backend import AGVBackend, AGVState, NavigationStatus


class FakeResponse:
    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


class FakeClient:
    def __init__(self):
        self.read_results = []
        self.calls = []

    def connect(self):
        return True

    def close(self):
        self.calls.append(("close",))

    def read_input_registers(self, address, *, count, device_id):
        self.calls.append(("read_input_registers", address, count, device_id))
        return FakeResponse(self.read_results.pop(0))

    def read_holding_registers(self, address, *, count, device_id):
        self.calls.append(("read_holding_registers", address, count, device_id))
        return FakeResponse([0] * count)

    def write_register(self, address, value, *, device_id):
        self.calls.append(("write_register", address, value, device_id))
        return FakeResponse()

    def write_registers(self, address, values, *, device_id):
        self.calls.append(("write_registers", address, list(values), device_id))
        return FakeResponse()


def state_registers(task_id, navigation_status, terminal_station=0, system_status=2):
    registers = [0] * 32
    registers[0] = terminal_station
    registers[1] = terminal_station
    registers[2] = task_id >> 16
    registers[3] = task_id & 0xFFFF
    registers[13] = navigation_status
    registers[31] = system_status
    return registers


def connected_backend(fake):
    backend = AGVBackend(_client=fake)
    with redirect_stdout(io.StringIO()):
        assert backend.connect()
    return backend


class AGVBackendTests(unittest.TestCase):
    def test_print_current_task_id_reads_high_word_then_low_word(self):
        fake = FakeClient()
        fake.read_results.append([0x1234, 0x5678])
        backend = connected_backend(fake)
        output = io.StringIO()

        with redirect_stdout(output):
            task_id = backend.print_current_task_id()

        self.assertEqual(task_id, 0x12345678)
        self.assertIn("十进制=305419896", output.getvalue())
        self.assertIn("高16位=4660", output.getvalue())
        self.assertIn("低16位=22136", output.getvalue())
        self.assertIn("0x12345678", output.getvalue())
        self.assertIn(("read_input_registers", 3, 2, 1), fake.calls)

    def test_set_task_id_writes_both_words_in_one_request(self):
        fake = FakeClient()
        backend = connected_backend(fake)

        backend.set_task_id(0x12345678)

        self.assertIn(
            ("write_registers", 50, [0x1234, 0x5678], 1), fake.calls
        )

    def test_navigate_to_writes_start_register_last(self):
        fake = FakeClient()
        fake.read_results.append(state_registers(7, NavigationStatus.ARRIVED))
        backend = connected_backend(fake)

        result = backend.navigate_to(12, wait=False)

        self.assertEqual(result.task_id, 8)
        self.assertEqual(result.target_station, 12)
        self.assertIsNone(result.success)
        write_calls = [call for call in fake.calls if call[0].startswith("write_")]
        self.assertEqual(
            write_calls,
            [
                ("write_registers", 50, [0, 8], 1),
                ("write_register", 52, 12, 1),
                ("write_register", 53, 0, 1),
                ("write_register", 54, 1, 1),
            ],
        )

    def test_wait_for_arrival_ignores_previous_task_arrived_state(self):
        fake = FakeClient()
        fake.read_results.extend(
            [
                state_registers(7, NavigationStatus.ARRIVED, terminal_station=12),
                state_registers(8, NavigationStatus.MOVING),
                state_registers(8, NavigationStatus.ARRIVED, terminal_station=12),
            ]
        )
        backend = connected_backend(fake)

        with patch("robot.agv_backend.time.sleep", return_value=None):
            result = backend.wait_for_arrival(8, 12)

        self.assertTrue(result.success)
        self.assertEqual(result.terminal_station, 12)
        read_calls = [
            call for call in fake.calls if call[0] == "read_input_registers"
        ]
        self.assertEqual(len(read_calls), 3)

    def test_state_reports_known_system_status_text(self):
        state = AGVState(0, 0, 1, NavigationStatus.FAILED, 18)

        self.assertEqual(state.navigation_status_text, "失败")
        self.assertEqual(state.system_status_text, "无法规划路径")

    def test_station_id_must_be_positive_integer(self):
        backend = AGVBackend(_client=FakeClient())

        for invalid in (0, -1, True, 1.5, "1"):
            with self.subTest(station_id=invalid):
                with self.assertRaises(ValueError):
                    backend.set_target_station(invalid)


if __name__ == "__main__":
    unittest.main()
