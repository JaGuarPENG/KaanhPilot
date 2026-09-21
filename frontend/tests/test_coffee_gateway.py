"""Coffee gateway contracts; only local demo devices are used."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import sys

# Support direct execution and test discovery from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import Store, APIError
from device_adapter import DemoDevice, RealDevice


class CoffeeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device = DemoDevice()
        self.store = Store(Path(self.temp.name) / 'tasks.json', self.device)

    def test_each_coffee_can_start_complete_and_deduplicate(self):
        for item, name in (('americano', '美式'), ('latte', '拿铁'), ('cappuccino', '卡布奇诺')):
            with self.subTest(item=item):
                self.assertEqual(self.store.snapshot()['items'].get(item),
                                 {'name': name, 'category': 'coffee', 'available': 1})
                body = {'item_id': item, 'request_id': 'coffee-' + item}
                task = self.store.grab(body)
                self.assertIn(task['task_id'], self.device.started)
                self.assertEqual(self.store.grab(body)['task_id'], task['task_id'])
                self.device.started[task['task_id']] -= 4
                self.store.tick()
                self.assertEqual(self.store.grab(body)['status'], 'completed')
                self.assertTrue(self.store.snapshot()['ready'])

    def test_coffee_action_and_sold_out_validation(self):
        with self.assertRaises(APIError):
            self.store.grab({'item_id': 'latte', 'action': 'grab_snack', 'request_id': 'coffee-invalid'})
        self.store.set_test_inventory({'item_id': 'latte', 'available': 0})
        with self.assertRaises(APIError) as caught:
            self.store.grab({'item_id': 'latte', 'action': 'make_coffee', 'request_id': 'coffee-soldout'})
        self.assertEqual(caught.exception.error_code, 'out_of_stock')
        self.store.set_test_inventory({'item_id': 'latte', 'available': 1})
        task = self.store.grab({'item_id': 'latte', 'action': 'make_coffee', 'request_id': 'coffee-available'})
        self.assertIn(task['task_id'], self.device.started)

    def test_real_adapter_forwards_coffee_identity(self):
        device = RealDevice()
        with patch.object(device, 'request') as request:
            device.start({'item_id': 'cappuccino', 'task_id': 'coffee-forward'})
        request.assert_called_once_with('/api/v1/picks',
                                        {'item_id': 'cappuccino', 'request_id': 'coffee-forward'})


if __name__ == '__main__':
    unittest.main()
