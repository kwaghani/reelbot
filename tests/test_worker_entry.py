"""The `--save` child is how every share is processed; it must reach process()."""
import sys, unittest
from unittest.mock import patch
from worker import worker


class SaveChildEntryTests(unittest.TestCase):
    def test_save_child_calls_process(self):
        with patch.object(sys, 'argv', ['worker', '--save', 'save-id']), patch.object(worker, 'validate_service_config'), \
             patch.object(worker, 'check_pool_capacity'), patch.object(worker.signal, 'alarm'), \
             patch.object(worker, 'process') as process:
            worker.main()
        process.assert_called_once_with('save-id')

    def test_main_has_no_local_that_shadows_process(self):
        self.assertNotIn('process', worker.main.__code__.co_varnames)


if __name__ == '__main__': unittest.main()
