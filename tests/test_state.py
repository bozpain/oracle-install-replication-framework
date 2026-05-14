import unittest
import tempfile
import uuid
from pathlib import Path

from oracle_auto.state import StateStore


class StateStoreTest(unittest.TestCase):
    def test_mark_done_persists(self):
        state_dir = Path(tempfile.gettempdir()) / "oracle-auto-tests" / "test-state"
        run_id = f"unit-{uuid.uuid4().hex}"
        first = StateStore(state_dir, run_id)
        first.mark_done("precheck:db1:ssh")

        second = StateStore(state_dir, run_id)
        self.assertTrue(second.is_done("precheck:db1:ssh"))


if __name__ == "__main__":
    unittest.main()
