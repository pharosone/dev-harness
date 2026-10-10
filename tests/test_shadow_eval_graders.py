"""Recorded formatting failure: a headed Compare section is still an explicit offer."""
import json
from pathlib import Path
import re
import unittest

GRADERS = Path(__file__).resolve().parents[1] / 'plugin/evals/compare-offer/graders'


def pattern(name):
    line = next(line for line in (GRADERS / (name + '.md')).read_text().splitlines()
                if line.startswith('pattern: '))
    return re.compile(json.loads(line.partition(': ')[2]), re.I)


class CompareHeadingRegressionTest(unittest.TestCase):
    def test_recorded_heading_and_existing_inline_forms_are_recognized(self):
        examples = [
            '**2. Compare**\n- **`design` (Recommended).** Two competing approaches.\n- `off` (default).',
            '**Compare**\n- `off` (default).\n- **`design` (Recommended).** Two approaches.',
            'compare=design (recommended), off by default',
            '`compare`: off by default; recommend compare=design',
        ]
        for name in ('offers-compare', 'recommends-compare'):
            for example in examples:
                with self.subTest(name=name, example=example):
                    self.assertRegex(example, pattern(name))

    def test_absent_or_off_only_recommendation_does_not_earn_design_recommendation(self):
        for example in ('Two options have different consequences.',
                        '**Compare**\n- **off** (Recommended).\n- design is also available.',
                        'The retry table is recommended. It avoids another dependency.'):
            self.assertNotRegex(example, pattern('recommends-compare'))


if __name__ == '__main__':
    unittest.main()
