#!/usr/bin/env python3
"""Public, harness-only feedback; stdlib only.

  draft --kind bug|friction|feature --title T [--from-file F]
  list [--summary | --offerable]
  offer SLUG                 print the one-time question and remember the offer
  decline SLUG               remember the user's refusal
  send SLUG [--yes] [--issue] [--not-a-duplicate] preview, deduplicate, then create an issue

Every command accepts --task T (task_state's primary-checkout run directory) or
--run-dir DIR (an existing run directory, including an overlay-owned run).
Without either, use <checkout>/.harness/feedback. project.json → feedback.redact
adds literal private terms. Draft files are sanitized at creation AND before sending;
review them anyway: pattern matching cannot recognize arbitrary product data.
--yes is only for an explicit user go after reviewing the final text. With local
vendored-core edits, send offers harness_sync.py propose instead; --issue explicitly
chooses an issue. No PR is published by this script. Exit 0 done/preview, 2 refusal/error.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_sync
import project
import task_state
from xreview import SECRET_VALUE

UPSTREAM = "pharosone/dev-harness"
HEADINGS = ("What happened", "Expected", "Repro (minimal, harness-only)", "Evidence", "Proposal")
MARKER = re.compile(r'^<!-- harness-feedback (.+) -->\n')
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PUBLIC_DOCS = {"docs.python.org": "/", "docs.github.com": "/",
               "learn.chatgpt.com": "/docs", "platform.openai.com": "/docs",
               "code.claude.com": "/docs", "docs.anthropic.com": "/", "herdr.dev": "/"}
URL = re.compile(r"(?:https?://|ssh://|git://|www\.)[^\s<>\"`]+", re.I)
EMAIL = re.compile(r"\b[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
# Assignments and authorization headers complement dashboard/xreview's value patterns.
ASSIGNMENT = re.compile(r'''(?im)(\b(?:[a-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[a-z0-9_]*|authorization)\b["']?\s*[:=]\s*)([^\n]+)''')
AUTH = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=-]+", re.I)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
REMAINING = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:sk-[A-Za-z0-9_-]{12,}|(?:gh[pousr]_|github_pat_|xox[abpr]-|AKIA|AIza|sk_live_)[A-Za-z0-9_-]+)")
# Consume placeholders and closing tags intact before looking for a path. A '>'
# can precede a real path in both HTML and shell redirections.
ABS_PATH = re.compile(r"<(?:harness|product)>/[^\s<>\"'`),;]*|</[A-Za-z][\w:-]*\s*>|(?P<path>(?<![\w/])(?:[A-Za-z]:[\\/]|/|\\\\)[^\s<>\"'`),;]+)")
QUOTED_PATH = re.compile(r'''(["'])(/[^\n"']*|[A-Za-z]:[\\/][^\n"']*|\\\\[^\n"']*)\1''')
# Task worktrees often share a name with a harness script or command. Such a
# name is ambiguous, so preserve harness vocabulary; paths are still scrubbed.
HARNESS_TERMS = {p.stem.casefold() for p in Path(__file__).resolve().parent.glob("*.py")} | set(UPSTREAM.split("/")) | {
    "harness", "core", "feedback", "freeze", "amend", "verify", "gate", "taste",
    "draft", "list", "offer", "decline", "send", "propose", "install", "pull",
    "status", "start", "stop", "run", "bug", "friction", "feature",
}


class FeedbackError(Exception):
    """A refusal; never include the sensitive input in the exception."""


def settings(root: Path) -> tuple[dict, list[str]]:
    config = project.load(root, "project.json", {})
    section = config.get("feedback", {})
    if not isinstance(section, dict) or set(section) - {"redact"}:
        raise FeedbackError("feedback must be an object containing only redact")
    terms = section.get("redact", [])
    if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise FeedbackError("feedback.redact must be a list of nonempty literal strings")
    names = [config.get("name", "")]
    if root.resolve() != project.core_root():
        names.append(root.name)
        primary, _ = task_state.checkouts()
        names.append(primary.name)
    return config, [t for t in names if isinstance(t, str) and t and t.casefold() not in HARNESS_TERMS]


def sanitize(text: str, root: Path) -> str:
    """Scrub recognized private data, then fail closed on unresolved secret patterns."""
    config, names = settings(root)
    terms = config.get("feedback", {}).get("redact", [])
    text = PEM.sub("[redacted]", text)
    if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY", text):
        raise FeedbackError("unclosed private key material; remove it before sending")
    text = SECRET_VALUE.sub("[redacted]", text)
    text = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[redacted]", text)
    text = JWT.sub("[redacted]", text)
    text = AUTH.sub("[redacted]", text)
    # Empty/multiline assignments are refused rather than guessing their boundaries.
    if re.search(r"(?im)\b(?:password|secret|token|api[_-]?key)\s*[:=][ \t]*$", text):
        raise FeedbackError("unresolved multiline secret assignment; remove it before sending")
    text = ASSIGNMENT.sub(lambda m: m[1] + "[redacted]", text)
    urls: list[str] = []

    def url(match: re.Match) -> str:
        raw = match[0].rstrip(".,)")
        suffix = match[0][len(raw):]
        try:
            parsed = urlsplit(raw if not raw.startswith("www.") else "https://" + raw)
            port = parsed.port
        except ValueError:
            return "[url]" + suffix
        host, path = parsed.hostname, parsed.path
        allowed = (host == "github.com" and
                   (path == "/" + UPSTREAM or path.startswith("/" + UPSTREAM + "/")))
        prefix = PUBLIC_DOCS.get(host or "")
        allowed = allowed or (prefix is not None and
                             (prefix == "/" or path == prefix or path.startswith(prefix + "/")))
        if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or not allowed:
            return "[url]" + suffix
        urls.append(urlunsplit(("https", host or "", path, "", "")))
        return f"{{{{PUBLICURL{len(urls) - 1}}}}}" + suffix

    text = URL.sub(url, text)

    def path(raw: str) -> str:
        try:
            candidate = Path(raw)
            if candidate.is_absolute() and candidate.resolve().is_relative_to(project.core_root()):
                return "<harness>/" + candidate.resolve().relative_to(project.core_root()).as_posix()
        except (OSError, ValueError):
            pass
        return "<product>/…"

    text = QUOTED_PATH.sub(lambda m: m[1] + path(m[2]) + m[1], text)
    text = ABS_PATH.sub(lambda m: path(m["path"]) if m["path"] else m[0], text)
    for number, allowed_url in enumerate(urls):
        text = text.replace(f"{{{{PUBLICURL{number}}}}}", allowed_url)
    text = EMAIL.sub("[email]", text)
    # IPv4 and IPv6 (including ::1, brackets and IPv4-mapped IPv6).
    def ip(match: re.Match) -> str:
        raw = match[0]
        try:
            ipaddress.ip_address(raw.split("%", 1)[0])
            return "[ip]"
        except ValueError:
            return raw

    text = re.sub(r"(?<![\w:])(?:[0-9A-Fa-f]*:){2,}[0-9A-Fa-f:.]*(?:%[\w.-]+)?(?!\w)|(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", ip, text)
    for name in sorted(set(names), key=len, reverse=True):
        text = re.sub(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", "[private]", text, flags=re.I)
    for term in sorted(set(terms), key=len, reverse=True):
        text = re.sub(re.escape(term), "[private]", text, flags=re.I)
    if SECRET_VALUE.search(text) or REMAINING.search(text) or JWT.search(text):
        raise FeedbackError("a secret pattern remains; remove it before sending")
    return text


def queue(root: Path, args: argparse.Namespace) -> Path:
    if args.task:
        primary, _ = task_state.checkouts()
        run = task_state.locate(primary, args.task)
    elif args.run_dir:
        run = Path(args.run_dir).resolve()
        if not run.is_dir():
            raise FeedbackError("--run-dir must be an existing run directory")
    else:
        run = root / ".harness"
    return run / "feedback"


def draft_path(folder: Path, slug: str) -> Path:
    if not SLUG.fullmatch(slug):
        raise FeedbackError("expected a feedback slug, not a path")
    path = folder / (slug + ".md")
    if path.is_symlink():
        raise FeedbackError("symlinked drafts are refused")
    return path


def read(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = MARKER.match(text)
    if not match:
        raise FeedbackError("draft metadata is missing")
    meta = json.loads(match[1])
    if not isinstance(meta, dict) or meta.get("status") not in ("draft", "declined", "sent"):
        raise FeedbackError("invalid draft status")
    return meta, text[match.end():].lstrip("\n")


def write(path: Path, meta: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement: interruption cannot truncate a queued draft.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        stream.write("<!-- harness-feedback " + json.dumps(meta, ensure_ascii=False) + " -->\n\n" + body)
    os.replace(temporary, path)


def title_of(body: str) -> str:
    first = body.splitlines()[0] if body.splitlines() else ""
    if not first.startswith("# ") or not first[2:].strip():
        raise FeedbackError("draft must start with a title: # Title")
    return first[2:].strip()


def new_draft(root: Path, folder: Path, args: argparse.Namespace) -> int:
    title = sanitize(args.title, root).replace("\n", " ").strip()
    if not title:
        raise FeedbackError("title must not be empty")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:90].rstrip("-") or "feedback"
    # Keep established short ASCII slugs. Hash titles that lose distinguishing
    # text through transliteration/truncation, or collide with another title.
    if not title.isascii() or len(title) > 90:
        slug = slug[:77].rstrip("-") + "-" + hashlib.sha256(title.encode()).hexdigest()[:12]
    path = draft_path(folder, slug)
    if path.exists():
        meta, body = read(path)
        if title_of(body) != title or meta.get("kind") != args.kind:
            suffix = hashlib.sha256((args.kind + "\0" + title).encode()).hexdigest()[:12]
            slug = slug[:77].rstrip("-") + "-" + suffix
            path = draft_path(folder, slug)
    if path.exists():
        # A repeated distinct-issue title retains its offered/declined/sent state.
        meta, body = read(path)
        if title_of(body) != title or meta.get("kind") != args.kind:
            raise FeedbackError("slug already exists; choose a distinct title")
        print(path)
        return 0
    config, _ = settings(root)
    pin = config.get("core", {})
    version = pin.get("ref", "unknown") if isinstance(pin, dict) else "unknown"
    source = Path(args.from_file).read_text(encoding="utf-8") if args.from_file else "Describe the harness behavior."
    if all("## " + h in source for h in HEADINGS):
        source = re.sub(r"\A# [^\n]*\n+", "", source)
        body = f"# {title}\n\n{source.strip()}\n"
    else:
        body = f"# {title}\n\n## What happened\n{source.strip()}\n\n## Expected\nDescribe the documented behavior.\n\n## Repro (minimal, harness-only)\nUse a synthetic checkout; no product data.\n\n## Evidence\n- Harness version: {version}\n- Script: unknown\n- Exit code: unknown\n- Sanitized output: not recorded\n\n## Proposal\nDescribe a harness improvement.\n"
    body = sanitize(body, root)
    write(path, {"kind": args.kind, "status": "draft", "offered": False}, body)
    print(path)
    return 0


def entries(folder: Path) -> list[tuple[Path, dict, str]]:
    return [(p, *read(draft_path(folder, p.stem))) for p in sorted(folder.glob("*.md"))]


def listing(root: Path, folder: Path, args: argparse.Namespace) -> int:
    drafts = entries(folder)
    if args.summary:
        waiting = [sanitize(title_of(body), root) for _, meta, body in drafts if meta["status"] == "draft"]
        print(f"{len(waiting)} harness feedback drafts waiting: " + ("; ".join(waiting) or "none"))
    else:
        for path, meta, body in drafts:
            if args.offerable and (meta["status"] != "draft" or meta.get("offered")):
                continue
            status = meta["status"] + (": " + sanitize(meta.get("url", "unknown"), root) if meta["status"] == "sent" else "")
            print(f"{path.stem}\t{status}\t{sanitize(title_of(body), root)}")
    return 0


def core_edits(root: Path, pin: dict) -> bool:
    if root == project.core_root():
        return False
    prefix = pin["prefix"]
    if harness_sync.porcelain(root, prefix):
        return True
    # No network needed: the last subtree squash is the sync baseline.
    base = harness_sync.last_squash(root, prefix)
    return harness_sync.value(root, "rev-parse", f"{base}^{{tree}}") != harness_sync.prefix_tree(root, prefix)


def browser_url(title: str, body: str) -> str:
    return "https://github.com/" + UPSTREAM + "/issues/new?" + urlencode({"title": title, "body": body})


def gh(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True)


def send(root: Path, folder: Path, args: argparse.Namespace) -> int:
    path = draft_path(folder, args.slug)
    meta, raw = read(path)
    if meta["status"] != "draft":
        print(f"{args.slug}: {meta['status']}; nothing sent")
        return 0
    body = sanitize(raw, root)
    title = title_of(body)
    # Persist the exact reviewed text before any external action.
    write(path, meta, body)
    print(body, flush=True)
    pin = harness_sync.read_pin(root)
    repo = harness_sync.repo_slug(pin["repo"])
    if repo != UPSTREAM:
        raise FeedbackError("feedback destination must be the public pharosone/dev-harness core pin")
    if not shutil.which("gh") or gh(root, "auth", "status").returncode:
        print("Not sent. Open and review the prefilled issue:\n" + browser_url(title, body))
        return 0
    duplicates = gh(root, "issue", "list", "-R", repo, "--state", "all", "--search", title,
                    "--json", "title,url", "--limit", "20")
    if duplicates.returncode:
        raise FeedbackError("duplicate check failed; nothing sent (retry later)")
    found = json.loads(duplicates.stdout)
    if not isinstance(found, list):
        raise FeedbackError("invalid duplicate response; nothing sent")
    if found:
        print("Possible duplicates:")
        for entry in found:
            # These are already public upstream titles, not outbound product
            # evidence. Validate links and suppress terminal control characters.
            link = entry["url"]
            if not re.fullmatch(r"https://github\.com/pharosone/dev-harness/issues/\d+", link):
                raise FeedbackError("invalid duplicate URL; nothing sent")
            public_title = "".join(c for c in entry["title"] if c.isprintable())
            print(f"{public_title}: {link}")
        if not args.not_a_duplicate:
            print("Nothing created. Review the links; if distinct, rerun send " + args.slug +
                  " --not-a-duplicate (add --yes only after user approval).")
            return 0
        print("User confirmed this is not a duplicate; publication still requires approval.")
    changed = False
    if not args.issue:
        try:
            changed = core_edits(root, pin)
        except harness_sync.SyncError:
            raise FeedbackError("cannot inspect the vendored core; choose --issue or install the core with subtree history") from None
    if changed:
        command = ["python3", str(project.core_root() / "scripts/harness_sync.py"), "propose",
                   "--branch", "fix/" + args.slug, "--title", title, "--feedback-file", str(path)]
        print("Local core edits may implement this fix. Offer: Prepare a harness PR instead?")
        print(shlex.join(command))
        print("This prepares a PR body with the reviewed draft attached; review the diff before publishing.")
        print("Choose an issue explicitly with send " + args.slug + " --issue (add --yes only after user approval).")
        return 0
    approved = args.yes
    if not approved and sys.stdin.isatty():
        try:
            approved = input("Send this reviewed harness feedback? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            approved = False
    if not approved:
        print("Not sent. Run send " + args.slug + " --yes only after the user's explicit go.")
        return 0
    # Use a body file: no shell interpolation, and the preview matches what gh receives.
    with tempfile.TemporaryDirectory(prefix="harness-feedback-") as temporary:
        body_file = Path(temporary) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        result = gh(root, "issue", "create", "-R", repo, "--title", title, "--body-file", str(body_file))
    if result.returncode:
        raise FeedbackError("issue creation failed; draft retained (check upstream before retrying)")
    url = result.stdout.strip()
    if not re.fullmatch(r"https://github\.com/pharosone/dev-harness/issues/\d+", url):
        raise FeedbackError("issue create returned an unexpected URL; check upstream before retrying")
    meta.update(status="sent", url=url, offered=True)
    write(path, meta, body)
    print(url)
    return 0


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--task")
    common.add_argument("--run-dir")
    commands = main.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", parents=[common])
    draft.add_argument("--kind", choices=("bug", "friction", "feature"), required=True)
    draft.add_argument("--title", required=True)
    draft.add_argument("--from-file")
    listing = commands.add_parser("list", parents=[common])
    view = listing.add_mutually_exclusive_group()
    view.add_argument("--summary", action="store_true")
    view.add_argument("--offerable", action="store_true")
    for name in ("offer", "decline", "send"):
        command = commands.add_parser(name, parents=[common])
        command.add_argument("slug")
        if name == "send":
            command.add_argument("--yes", action="store_true")
            command.add_argument("--issue", action="store_true")
            command.add_argument("--not-a-duplicate", action="store_true",
                                 help="user confirms the displayed candidate issues are distinct; still requires approval")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.task and args.run_dir:
            raise FeedbackError("choose --task or --run-dir")
        root = project.repo_root()
        folder = queue(root, args)
        if args.command == "draft":
            return new_draft(root, folder, args)
        if args.command == "list":
            return listing(root, folder, args)
        if args.command == "send":
            return send(root, folder, args)
        path = draft_path(folder, args.slug)
        meta, raw = read(path)
        body = sanitize(raw, root)
        if args.command == "decline":
            if meta["status"] == "sent":
                raise FeedbackError("sent feedback cannot be declined")
            meta.update(status="declined", offered=True)
            write(path, meta, body)
            print(f"{args.slug}: declined")
        elif meta["status"] == "draft" and not meta.get("offered"):
            meta["offered"] = True
            write(path, meta, body)
            print(f'Offer harness feedback “{title_of(body)}” for your review?')
        return 0
    except FeedbackError as exc:
        print(f"feedback: {exc}", file=sys.stderr)
        return 2
    except (project.OverlayError, harness_sync.SyncError, task_state.StateError,
            OSError, ValueError, KeyError, TypeError):
        # Parser/IO errors may contain product paths or secret input: keep diagnostics generic.
        print("feedback: refused or failed; check the pin, draft, redact settings and secret patterns; nothing further sent", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
