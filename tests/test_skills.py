"""Skills that start work on a task offer the depth mode, and the investigation skill exists."""
from pathlib import Path
import re
import unittest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
MODE_SKILLS = ("pharos-investigate", "pharos-discover", "pharos-dev")


class DepthModeTest(unittest.TestCase):
    def test_the_starting_skills_offer_standard_or_deep_with_a_recommendation(self):
        for name in MODE_SKILLS:
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertRegex(text, r"`standard`")
                self.assertRegex(text, r"`deep`")
                self.assertIn("deep-reasoning", text)
                self.assertRegex(text, r"\(Recommended\)")

    def test_investigate_names_its_three_kinds_of_question(self):
        text = (SKILLS / "pharos-investigate" / "SKILL.md").read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        self.assertRegex(head, r"name: pharos-investigate")
        for kind in (r"bug", r"decision", r"understand"):
            self.assertRegex(head.lower(), kind)

    def test_deep_mode_gets_a_second_vendor_opinion(self):
        for name in MODE_SKILLS:
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertRegex(text, r"other vendor|second opinion")


if __name__ == "__main__":
    unittest.main()
