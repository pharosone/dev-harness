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


class ShadowOwnershipRegressionTest(unittest.TestCase):
    def test_explicit_static_authority_and_shadow_exclusion_with_evidence(self):
        path = GRADERS.parent.parent / 'design-review-shape/graders/shadow-boundary.md'
        line = next(line for line in path.read_text().splitlines() if line.startswith('pattern: '))
        compiled = re.compile(json.loads(line.partition(': ')[2]), re.I)
        examples = [
            'Every cell is file:line. Dispatch must stay with the static balance selector, '
            'and the shadow scorer must never decide it. Include scorer output consumers.',
            'The static balance selector should own it, and the shadow scorer should only observe. '
            'Show file:line evidence on both sides and consumer grep.',
        ]
        for example in examples:
            self.assertRegex(example, compiled)
        for example in ('The shadow scorer owns live dispatch. Include consumer grep.',
                        'The static balance selector remains. The shadow scorer chooses the actual model. '
                        'Show file:line evidence.',
                        'The static balance selector dispatches; the shadow scorer is advisory only.'):
            self.assertNotRegex(example, compiled)


if __name__ == '__main__':
    unittest.main()
