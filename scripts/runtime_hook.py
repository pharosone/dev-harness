#!/usr/bin/env python3
"""Claude/Codex Stop adapter; only this layer remembers findings per session.

The check is check_docs_drift.py's, with the mappings of the checkout's overlay
(drift.json in $HARNESS_PROJECT, else harness/project; without it only the harness-log
rule runs). It adds no overlay keys. An unusable overlay file blocks (exit 2) like any
failed check: it is never reported clean.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from check_docs_drift import CheckError, check, default_base, format_findings, repository


def already_reported(root, runtime, session, findings):
    """Best-effort cache inside this checkout, without following cache symlinks.

    Git metadata may be read-only in an agent sandbox. Descriptor-relative opens
    also keep a replaced cache directory from redirecting writes outside the
    checkout. If caching is unavailable, always report the actual findings.
    """
    session_key = hashlib.sha256(f"{runtime}\0{session}".encode()).hexdigest()
    key = hashlib.sha256(json.dumps(findings, sort_keys=True).encode()).hexdigest()
    try:
        with ExitStack() as stack:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, directory)
            for name in (".harness", "cache"):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                directory = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                stack.callback(os.close, directory)
            descriptor = os.open(
                f"docs-drift-{session_key}",
                os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                mode=0o600, dir_fd=directory,
            )
            with os.fdopen(descriptor, "a+", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return False
                stream.seek(0)
                if key in stream.read().splitlines():
                    return True
                stream.write(key + "\n")
    except (OSError, ValueError):
        # Optional caching must not replace the drift report with a cache error.
        pass
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if event.get("stop_hook_active"):
            print("{}")
            return 0
        root = repository(event.get("cwd") or Path.cwd())
        findings = check(root, default_base(root))
        if findings:
            session = event.get("session_id")
            # Without a session identity do not suppress independent invocations.
            if session is not None:
                if not isinstance(session, str):
                    raise ValueError("session_id must be a string")
                if already_reported(root, args.runtime, session, findings):
                    print("{}")
                    return 0
            print(format_findings(findings), file=sys.stderr)
            return 2
    except (CheckError, OSError, ValueError) as error:
        print(f"Documentation drift hook failed: {error}", file=sys.stderr)
        return 2
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
