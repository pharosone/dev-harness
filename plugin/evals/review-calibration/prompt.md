---
name: review-calibration
description: Mixed findings from one product day; recorded failure — narrow Majors exhausted review while an approval risk was called Minor.
runs: 1
max_turns: 5
timeout_seconds: 180
expected_outcome: Triages using product likelihood/impact and code evidence, limits review to two milestone rounds, and preserves backlog/outcome feedback.
---
Load pharos-dev if available to triage this review before fix-up. If unavailable, use general
judgment without searching for the missing skill. Treat the supplied overlay and code excerpts
as complete scenario inputs; do not inspect the synthetic run directory, dispatch workers or
execute commands. Render ID: block|backlog lines as plain text, with no Markdown decoration
around IDs, colons or decision words. The output is consumed by a literal-text checker.
This is a pilot product; the overlay's only review invariant is "No private data reaches public issues".
Run directory: /tmp/calibration-run. Milestone: sending. Round 1 just finished.
All findings were labelled Major except F03 (Minor). Treat these labels as reviewer opinions.
The following sanitized code excerpts were read for this review; use them to check scenarios.

F01 — plausible/severe: failed LinkedIn reads keep re-queuing until the daily cap is spent.
Reachable: normal scheduled read → worker failure → retry queue.
Code-read evidence: src/read_worker.py:41 `except ReadError: queue.append(job)`; the daily
budget check at src/budget.py:19 counts every attempt. No retry bound. No workaround.

F02 — will/functional: an integration's MCP tools are never admitted.
Reachable: normal enabled integration → registry → tool filter.
Evidence: src/tool_filter.py:22 `return tool.name in CORE_TOOLS`; registry adds integration
names at src/registry.py:37. Integration tools never enter CORE_TOOLS. No workaround.

F03 — plausible/severe: proposal tap increments the auto-approval count although the card hides text.
Reachable: proposal notification → tap → approval counter → outbound send.
Evidence: src/proposals.py:62 `on_tap: approvals += 1`; src/card.py:18 renders only a title;
src/send.py:71 sends at threshold without another confirmation. No workaround.

F04 — will/functional: finished lanes never close in the watcher.
Reachable: normal lane completion → watcher tick → lane stays active.
Evidence: src/watch.py:35 `if state == "running": update(lane)` has no terminal-state branch;
src/lanes.py:48 retains lanes until `close(lane)`. No workaround.

F05 — edge/functional: watcher falsely reports exited in the first seconds of a launch.
Reachable: operator launch → watcher tick before PID registration → exited.
Evidence: src/watch.py:29 `if pid is None: state = "exited"`; launch registers PID later at
src/launch.py:50. Only a narrow launch race; restarting is a workaround.

F06 — edge/severe: private absolute path after `>` reaches a public issue.
Reachable: unusual pasted path → issue renderer → public issue creation.
Evidence: src/redact.py:12 checks tokens starting with `/`; src/issue.py:25 splits only on
spaces, so `>/private/client-notes` bypasses redaction before publish at src/issue.py:44.
Breaks the exact overlay invariant above. No workaround.

F07 — edge/functional: --issue fails in a shallow clone.
Reachable: unusual shallow checkout → issue CLI → ancestor lookup.
Evidence: src/issue_cli.py:32 `git merge-base HEAD origin/main` errors when history is missing.
Normal checkouts have history; fetching history is a workaround.

F08 — plausible/functional: duplicate check blocks sending forever; editing title allows sending.
Reachable: compose → duplicate-title check → send disabled.
Evidence: src/duplicates.py:24 matches historical titles without expiry; src/compose.py:53
lets the user edit the title, causing the duplicate check to pass. Specific workaround: edit title.

F09 — plausible/severe: missing optional assistant login prevents the critical path from starting.
Reachable: normal primary startup with optional assistant unconfigured → auth gate → process exit.
Evidence: src/startup.py:16 `if not assistant_logged_in: exit(1)` precedes primary runner init
at src/startup.py:27, silently disabling the required monitoring guard. No workaround.

F10 — reviewer claims will/severe, but supplies no code-read evidence or reachable path.
The review says only "remembered an auth risk".

Return:
1. One line per ID in `F01: block|backlog — reason` form, for F01–F10.
2. The ingestion command for /tmp/calibration-run/reports/security-r1.md before triage, and
   the triage command recording F05's downgrade and its code-grounded reason; say what must
   be verified before fix-up, which Minors join fix-up, and where other Minors are saved.
3. Round-2 decisions: R01 is a NEW will/functional failure with no workaround; R02 is a NEW
   plausible/severe unapproved outbound send; R03 is the unchanged verified F02 blocker.
   For each use `R01: block|backlog — reason`. Say what round 2 examines and whether an
   automatic third round is allowed after F02 survives.
4. What G4/final reports show, and the commands to record an observed outcome, report results,
   and export a sanitized feedback fixture. The user explicitly accepted F05 as backlog;
   show how to record that override alone when no later outcome is observed. Do not fabricate
   an outcome. Explain how multiple lenses share a round and which repeated submissions fail.
