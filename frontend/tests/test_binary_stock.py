import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import sys

# Support direct execution and test discovery from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store, APIError
from device_adapter import DemoDevice


class FrontendStockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device = DemoDevice()
        self.store = Store(Path(self.temp.name)/'history.json', self.device)

    def test_sold_out_rejected_before_dispatch(self):
        self.assertTrue(hasattr(self.store,'set_test_inventory'), 'stock proxy missing')
        self.store.set_test_inventory({'item_id':'water','available':0})
        self.assertEqual(self.store.snapshot()['items']['water']['available'],0)
        with self.assertRaises(APIError) as caught:
            self.store.grab({'item_id':'water','request_id':'stock-0001'})
        self.assertEqual(caught.exception.error_code,'out_of_stock')
        self.assertEqual(self.store.task_history()['total'],0)

    def test_confirmation_failure_survives_task_poll_and_restart(self):
        self.assertTrue(hasattr(self.store,'set_test_inventory'), 'stock proxy missing')
        self.store.set_test_inventory({'item_id':'water','available':1,'confirmation_available':0})
        task = self.store.grab({'item_id':'water','request_id':'stock-0002'})
        self.store.tick()
        result=self.store.task_history()['tasks'][0]
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'out_of_stock')
        self.assertEqual(self.store.snapshot()['items']['water']['available'],0)
        self.assertIsNone(self.store.active())
        self.assertEqual(self.store.grab({'item_id':'water','request_id':'stock-0002'})['task_id'],task['task_id'])
        reloaded=Store(self.store.task_history_path,self.device)
        self.assertEqual(reloaded.task_history()['tasks'][0]['error_code'],'out_of_stock')

    def test_test_write_forbidden_without_backend_dry_run(self):
        self.assertTrue(hasattr(self.store,'set_test_inventory'), 'stock proxy missing')
        with patch.object(self.device,'inventory',return_value={'dry_run':False,'revision':0,'items':{}}):
            with self.assertRaises(APIError) as caught:
                self.store.set_test_inventory({'item_id':'water','available':0})
        self.assertEqual(caught.exception.code,403)

    def test_inventory_unavailable_disables_grabbing_not_false_sold_out(self):
        self.assertTrue(hasattr(self.device,'inventory'), 'stock source missing')
        with patch.object(self.device,'inventory',side_effect=TimeoutError):
            snapshot=self.store.snapshot()
        self.assertFalse(snapshot['ready'])
        self.assertIsNone(snapshot['items']['water']['available'])

if __name__ == '__main__':
    unittest.main()
