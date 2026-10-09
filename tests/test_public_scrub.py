"""The core is public: no product names, hosts or stack-specific facts of any consumer.

Projects keep those in their own `harness/project` layer. The skill names pharos-dev and
pharos-discover are the core's own names and stay allowed.
"""
from pathlib import Path
import re
import subprocess
import unittest

CORE = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(
    r"vector-kya|pharosone(?!/dev-harness\b)|pharos(?!-dev\b|-discover\b|-investigate\b| task=|one/dev-harness\b)|meridian|\bclerk\b|\bsqlc\b|\bhuma\b|\bminio\b"
    r"|server/internal|web/lib|organization_id|\bcabinet\b|\bjudge\b",
    re.IGNORECASE)
SKIP = {"LICENSE", "tests/test_public_scrub.py"}


class PublicScrubTest(unittest.TestCase):
    def test_no_consumer_specifics_in_tracked_files(self):
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=CORE, capture_output=True, check=True)
        hits = []
        for name in filter(None, listed.stdout.decode().split("\0")):
            path = CORE / name
            if name in SKIP or path.is_symlink() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if FORBIDDEN.search(line):
                    hits.append(f"{name}:{number}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "consumer-specific text in the public core:\n" + "\n".join(hits[:40]))


if __name__ == "__main__":
    unittest.main()
