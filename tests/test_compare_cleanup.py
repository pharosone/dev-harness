"""compare removes only its own round's candidate snapshots."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare  # noqa: E402


class RoundCleanupTest(unittest.TestCase):
    def test_cleanup_is_filtered_to_this_rounds_candidates(self):
        run = mock.Mock(run=Path("/tmp/run"), primary=Path("/tmp"))
        launches = {"candidate-claude": {"dispatch": "d1"}, "candidate-codex": {"dispatch": "d2"},
                    "arbiter-1": {"dispatch": "d3"}}
        with mock.patch.object(compare, "collect", return_value={"launch": launches}), \
                mock.patch.object(compare.lanes, "snapshot_cleanup", return_value=[]) as cleanup:
            compare.remove_snapshots(run, "cmp-1")
        cleanup.assert_called_once()
        self.assertEqual(sorted(cleanup.call_args.kwargs.get("dispatches")
                                or cleanup.call_args.args[1]), ["d1", "d2"])


if __name__ == "__main__":
    unittest.main()
