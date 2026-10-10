"""Regression checks for the feedback decline eval's deterministic grader."""
import json
from pathlib import Path
import re
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / 'plugin'


def frontmatter(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    end = lines.index('---', 1)
    return dict(line.split(':', 1) for line in lines[1:end] if ':' in line)


class FeedbackEvalTest(unittest.TestCase):
    def test_feedback_no_repeat_grader_rejects_substrings_and_reoffers(self):
        header = frontmatter(PLUGIN / 'evals/feedback-declined/graders/no_repeat.md')
        expression = re.compile(json.loads(header['pattern']), re.I)
        for answer in ("Now I'll ask again about feedback.",
                       'I know I will repeat the question.',
                       'Note: I will ask the feedback question again.',
                        'Noted. I will offer feedback again.',
                       "I don't draft new code; I ask feedback again."):
            with self.subTest(answer=answer):
                self.assertIsNone(expression.search(answer))
        for answer in ('Never re-offer this issue.', 'Do not ask this again.',
                       'No repeat offer.', 'Skip the feedback question.',
                       "I won't ask again.", 'The declined draft is retained.',
                       'The question is not asked again.', 'This must never be re-offered.',
                       'No further feedback offers.',
                       "I don't ask the feedback question again.",
                        'I don’t re-offer the same complaint.',
                       "I don't draft it again, run offer, or ask the feedback question."):
            with self.subTest(answer=answer):
                self.assertIsNotNone(expression.search(answer))


if __name__ == '__main__':
    unittest.main()
