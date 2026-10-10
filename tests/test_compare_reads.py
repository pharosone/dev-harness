"""compare reports how candidates are confined after lanes' snapshot and shell sandbox, and
that what they can read is not confined."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare  # noqa: E402


class CapabilityNoteTest(unittest.TestCase):
    def test_the_note_names_the_shell_sandbox_and_the_read_risk(self):
        note = compare.capability_note([{"runtime": "claude"}, {"runtime": "codex"}])
        self.assertIn("sandbox", note.split(";")[0])
        self.assertNotIn("not path-scoped", note)
        self.assertIn("independence rests on the brief", note)

    def test_one_runtime_still_states_the_read_risk(self):
        self.assertIn("independence rests on the brief",
                      compare.capability_note([{"runtime": "codex"}]))


if __name__ == "__main__":
    unittest.main()
