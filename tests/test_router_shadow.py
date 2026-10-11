"""Shadow integration uses fixtures and fake CLIs only; no classifier experiment."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import router_shadow as shadow
import lanes
import project
import task_state


CONFIG = {"enabled": True, "model": "claude-haiku-5-5", "effort": "medium",
          "timeout_seconds": 2, "max_budget_usd": 0.05, "cache": True}
ANSWER = {"taxonomy_version": "1.1", "status": "classified",
          "weights": {"terminal": 0.6, "tool_workflow": 0.25, "review": 0.15},
          "reason": "Infrastructure and permissions."}
ACTUAL = {"runtime": "claude", "model": "opus", "effort": "high"}


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.overlay = self.root / "harness/project"
        self.overlay.mkdir(parents=True)
        self.run = self.root / ".harness/tasks/demo"
        self.run.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"HARNESS_PROJECT": str(self.overlay)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def configure(self, **values):
        (self.overlay / "router.json").write_text(json.dumps({"mode": "shadow", **values}))

    def capsule(self):
        return shadow.sanitize_capsule({"goal": "Isolate the runtime", "paths": ["infra/app.yaml"],
            "acceptance_criteria": ["No published ports"]}, role="implementer",
            profile="critical-review", tier=3)

    def record(self, kind, **values):
        entry = task_state.event_record(kind, json.dumps(values), None)
        task_state.append_event(self.run / "events.jsonl", entry)

    def evaluate(self, **values):
        values.setdefault("usage", {})
        return shadow.evaluate(self.root, self.run, self.root, actual=ACTUAL,
            profile="critical-review", role="implementer", tier=3, **values)

    def fake_scorer(self, classifier=None):
        context = {"policy": {"classifier": classifier or {**CONFIG, "enabled": False}},
                   "policy_hash": "p" * 64, "data_hash": "d" * 64}
        result = {"status": "scored", "recommendation": {
            "runtime": "codex", "model": "gpt-6-astra", "effort": "xhigh"},
            "reason": "Lower expected cost", "candidates": [], "assumptions": ["uncalibrated"]}
        module = types.SimpleNamespace(load_context=lambda root: context,
                                       score=lambda *args, **kwargs: result)
        return patch.dict(sys.modules, {"model_router": module})

    def test_off_has_no_data_reads_classifier_or_events(self):
        with patch.object(shadow, "classify", side_effect=AssertionError("paid call")):
            self.assertIsNone(self.evaluate(brief="missing-file"))
        self.assertEqual(list(self.run.iterdir()), [])

    def test_launcher_decision_outside_checkout_keeps_old_contract(self):
        with patch.object(project, "repo_root", side_effect=project.OverlayError("outside git")):
            self.assertIsNone(lanes.shadow_for_args(argparse.Namespace(), "lanes.decide"))

    def test_manual_weights_keep_actual_and_append_full_provenance(self):
        self.configure()
        with self.fake_scorer(), patch.object(shadow, "classify", side_effect=AssertionError):
            result = self.evaluate(weights=json.dumps(ANSWER["weights"]), usage={})
        self.assertEqual(result["actual"], ACTUAL)
        self.assertEqual(result["selected_tuple"], ACTUAL)
        self.assertEqual(result["recommendation"]["runtime"], "codex")
        self.assertEqual(result["classifier"]["validation"], "CALLER_SUPPLIED")
        self.assertEqual(result["policy_hash"], "p" * 64)
        shadow.append(self.run, self.root, result)
        shadow.append(self.run, self.root, result)
        events, _ = task_state.read_events(self.run)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "router.shadow")

    def test_enabled_shadow_does_not_imply_enabled_classifier(self):
        self.configure()
        with self.fake_scorer(), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate()
        self.assertEqual(result["status"], "skipped")
        self.assertIn("classifier disabled", result["reason"])
        self.assertIsNone(result["recommendation"])

    def test_classifier_failure_skips_and_does_not_repair(self):
        self.configure()
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier",
                side_effect=shadow.ShadowError("classifier timeout")) as call:
            result = self.evaluate(capsule=self.capsule())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["actual"], ACTUAL)
        self.assertEqual(result["classifier"]["validation"], "UNVALIDATED")
        self.assertEqual(result["status"], "skipped")

    def test_known_quota_stop_blocks_fresh_classifier_but_not_a_valid_cache(self):
        self.configure()
        usage = {"claude": {"status": "known", "windows": [
            {"name": "five_hour", "used": .9, "resets_at": 2000}]}}
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate(capsule=self.capsule(), usage=usage, now=1000)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("stop_at", result["reason"])
        self.assertFalse(result["classifier"]["attempted"])
        self.assertEqual(result["classifier"]["api_equivalent_usd"], 0)
        with patch.object(shadow, "invoke_classifier", return_value={
                "result": ANSWER, "resolved_model": CONFIG["model"]}):
            shadow.classify(CONFIG, self.capsule(), self.run, self.root)
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate(capsule=self.capsule(), usage=usage, now=1000)
        self.assertTrue(result["classifier"]["cache_hit"])
        self.assertEqual(result["classifier"]["api_equivalent_usd"], 0)

    def test_no_eligible_actual_dispatch_never_starts_fresh_classifier(self):
        self.configure()
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = shadow.evaluate(self.root, self.run, self.root,
                actual={"runtime": None, "model": None, "effort": None}, profile="critical-review",
                role="implementer", tier=3, capsule=self.capsule(), usage={})
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["classifier"]["attempted"])

    def test_failed_classifier_resource_evidence_is_logged(self):
        self.configure()
        telemetry = {"attempted": True, "usage": {"input_tokens": 123, "output_tokens": 20},
                     "api_equivalent_usd": .027, "elapsed_seconds": 1}
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier",
                side_effect=shadow.ClassifierFailure("classifier process failed", telemetry)):
            result = self.evaluate(capsule=self.capsule())
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["classifier"]["usage"], telemetry["usage"])
        self.assertEqual(result["classifier"]["api_equivalent_usd"], .027)

    def test_bad_configuration_never_breaks_static_dispatch(self):
        self.configure(mode="active")
        result = self.evaluate()
        self.assertEqual(result["actual"], ACTUAL)
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result["recommendation"])

    def test_missing_tier_is_not_downgraded_to_zero(self):
        self.configure()
        with self.fake_scorer():
            result = shadow.evaluate(self.root, self.run, self.root, actual=ACTUAL,
                                     profile="routine", role="implementer", tier=None)
        self.assertIn("explicit profile and tier", result["reason"])

    def test_codex_only_never_launches_claude_classifier(self):
        self.configure()
        self.record("run.start", runtime_mode="codex-only")
        with self.fake_scorer(CONFIG), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate(capsule=self.capsule())
        self.assertEqual(result["status"], "skipped")
        self.assertIn("runtime mode", result["reason"])

    def test_non_claude_classifier_is_not_sent_to_claude_cli(self):
        self.configure()
        with self.fake_scorer({**CONFIG, "model": "gpt-6-luna"}), patch.object(
                shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate(capsule=self.capsule())
        self.assertEqual(result["status"], "skipped")
        self.assertIn("no subscription adapter", result["reason"])

    def test_bad_manual_weights_are_not_a_paid_repair(self):
        self.configure()
        with self.fake_scorer(), patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            result = self.evaluate(weights='{"terminal": NaN}')
        self.assertEqual(result["status"], "skipped")

    def test_single_runtime_mode_passes_to_scorer(self):
        self.configure()
        self.record("run.start", runtime_mode="codex-only")
        self.assertEqual(shadow.runtime_mode(self.run), ["codex"])
        self.record("run.start", runtime_mode="unknown")
        with self.assertRaises(shadow.ShadowError):
            shadow.runtime_mode(self.run)

    def test_explicit_fixup_chain_and_prior_pick_links(self):
        self.record("dispatch.start", dispatch="first", root_attempt_id="first")
        self.record("dispatch.start", dispatch="second", root_attempt_id="first",
                    parent_attempt_id="first")
        self.record("router.shadow", decision_id="choice", actual=ACTUAL)
        result = shadow.validated_links(self.run, ACTUAL, "second", None, "choice")
        self.assertEqual(result["root_attempt_id"], "first")
        for parent, root, decision in (("missing", None, None), ("second", "second", None),
                                        (None, "first", None), (None, None, "missing")):
            with self.assertRaises(shadow.ShadowError):
                shadow.validated_links(self.run, ACTUAL, parent, root, decision)

    def test_bad_lineage_is_not_attached_to_dispatch(self):
        self.configure()
        with self.fake_scorer():
            result = self.evaluate(weights=ANSWER["weights"], parent_attempt_id="missing")
        self.assertIsNone(result["parent_attempt_id"])
        self.assertEqual(result["status"], "skipped")

    def test_dispatch_fields_do_not_copy_recommendation(self):
        self.assertEqual(shadow.dispatch_fields(None), {})
        fields = shadow.dispatch_fields({"decision_id": "id", "selected_tuple": ACTUAL,
                                         "recommendation": {"runtime": "codex"}})
        self.assertEqual(fields["selected_tuple"], ACTUAL)
        self.assertNotIn("recommendation", fields)

    def test_completion_identity_is_observed_not_guessed_from_alias(self):
        path = self.run / "worker.log"
        state = {"router_decision_id": "decision", "attempt_id": "a", "log": str(path),
                 "model": "opus", "effort": "high"}
        unknown = shadow.completion_fields(state)["serving_identity"]
        self.assertEqual(unknown["status"], "unverified")
        self.assertIsNone(unknown["resolved_model"])
        path.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5"}}))
        known = shadow.completion_fields(state)["serving_identity"]
        self.assertEqual(known["resolved_model"], "claude-opus-5-5")
        self.assertIsNone(known["resolved_effort"])
        with path.open("a") as stream:
            stream.write("\n" + json.dumps({"type": "result", "modelUsage": {"claude-sonnet-5-5": {}}}))
        self.assertEqual(shadow.completion_fields(state)["serving_identity"]["status"], "mixed")
        self.assertEqual(shadow.completion_fields({}), {})

    def test_cache_stores_no_task_text_and_invalid_cache_skips(self):
        response = {"result": ANSWER, "resolved_model": CONFIG["model"],
                    "usage": {"input_tokens": 10}, "elapsed_seconds": 1,
                    "api_equivalent_usd": 0.001}
        with patch.object(shadow, "invoke_classifier", return_value=response) as call:
            first = shadow.classify(CONFIG, self.capsule(), self.run, self.root)
            second = shadow.classify(CONFIG, self.capsule(), self.run, self.root)
            self.assertEqual(call.call_count, 1)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        cache = next((self.run / "router-cache").glob("*.json"))
        self.assertNotIn("Isolate the runtime", cache.read_text())
        cache.write_text('{"cache_key":"wrong"}')
        with patch.object(shadow, "invoke_classifier", side_effect=AssertionError):
            with self.assertRaisesRegex(shadow.ShadowError, "cache invalid"):
                shadow.classify(CONFIG, self.capsule(), self.run, self.root)

    def test_cache_key_changes_with_effort_or_capsule(self):
        response = {"result": ANSWER, "resolved_model": CONFIG["model"]}
        with patch.object(shadow, "invoke_classifier", return_value=response) as call:
            shadow.classify(CONFIG, self.capsule(), self.run, self.root)
            shadow.classify({**CONFIG, "effort": "high"}, self.capsule(), self.run, self.root)
            shadow.classify(CONFIG, {**self.capsule(), "goal": "other"}, self.run, self.root)
        self.assertEqual(call.call_count, 3)

    def test_cache_write_failure_preserves_classification_and_cost(self):
        response = {"result": ANSWER, "resolved_model": CONFIG["model"], "api_equivalent_usd": .027}
        with patch.object(shadow, "invoke_classifier", return_value=response), patch.object(
                shadow.os, "replace", side_effect=OSError("readonly")):
            result = shadow.classify(CONFIG, self.capsule(), self.run, self.root)
        self.assertEqual(result["api_equivalent_usd"], .027)
        self.assertEqual(result["result"], ANSWER)
        self.assertEqual(result["cache_write_error"], "OSError")

    def test_cache_cannot_follow_symlink(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.run / "router-cache").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(task_state.StateError):
            shadow.classify(CONFIG, self.capsule(), self.run, self.root)


class CapsuleTest(unittest.TestCase):
    def test_capsule_allowlist_and_common_redaction(self):
        capsule = shadow.sanitize_capsule({"goal": "Read https://example.test/a and user@example.test "
            "at /private/work/file; API_KEY=secret-value", "paths": ["src/test.py", "/secret/a", "../a"],
            "acceptance_criteria": ["Tests pass"], "credentials": "never-send",
            "report": "/private/report.md", "chat_history": "never-send"},
            role="implementer", profile="routine", tier=1)
        text = json.dumps(capsule)
        for absent in ("never-send", "secret-value", "user@example", "/private/work", "https://"):
            self.assertNotIn(absent, text)
        self.assertEqual(capsule["paths"], ["src/test.py"])

    def test_capsule_rejects_oversize_and_boolean_tier(self):
        for value, tier in (({"goal": "x" * 8193}, 1), ({"goal": "x"}, True)):
            with self.assertRaises(shadow.ShadowError):
                shadow.sanitize_capsule(value, role="worker", profile="routine", tier=tier)

    def test_markdown_only_known_sections_no_reference_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            path.write_text("# Work\nowner private identity\n## Goal\nFix queue\n"
                            "## Contract\n- Jobs run once\n## Report\nsecret destination\n")
            capsule = shadow.capsule_from_brief(path)
            self.assertEqual(capsule["goal"], "Fix queue")
            self.assertEqual(capsule["acceptance_criteria"], ["Jobs run once"])
            self.assertNotIn("private", json.dumps(capsule))
            path.write_text("# No explicit goal\nread referenced file\n")
            self.assertEqual(shadow.capsule_from_brief(path)["goal"], "No explicit goal")

    def test_missing_goal_uses_title_without_reading_unscoped_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            path.write_text("# Repair queue delivery\nprivate background\n"
                            "## Contract\n- Deliver once\n## Report\nsecret path\n")
            capsule = shadow.capsule_from_brief(path)
            self.assertEqual(capsule["goal"], "Repair queue delivery")
            self.assertEqual(capsule["acceptance_criteria"], ["Deliver once"])
            self.assertNotIn("private", json.dumps(capsule))

    def test_goal_section_takes_precedence_and_empty_goal_is_not_inferred(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            path.write_text("# Repair queue\n## Goal\nDeliver once\n")
            self.assertEqual(shadow.capsule_from_brief(path)["goal"], "Deliver once")
            path.write_text("# Repair queue\n## Goal\n## Contract\n- Deliver once\n")
            with self.assertRaises(shadow.ShadowError):
                shadow.capsule_from_brief(path)

    def test_fenced_heading_is_not_a_title_or_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            path.write_text("```markdown\n# Ignore code title\n## Goal\nsecret text\n```\n"
                            "# Repair queue\n## Contract\n- Deliver once\n")
            capsule = shadow.capsule_from_brief(path)
            self.assertEqual(capsule["goal"], "Repair queue")
            self.assertNotIn("secret", json.dumps(capsule))

    def test_prefixed_level_one_sections_preserve_title_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            for goal_heading in ("Goal: ship X", "Objective: ship X"):
                path.write_text(f"# Project title\n# {goal_heading}\nShip queue\n"
                                "# Acceptance criteria:\n- Once\n# Allowed paths:\n- src/queue.py\n")
                self.assertEqual(shadow.capsule_from_brief(path), {
                    "goal": "Ship queue", "paths": ["src/queue.py"],
                    "acceptance_criteria": ["Once"]})
            path.write_text("# Project title\n# Acceptance: delivery\n- Once\n# Paths:\n- src/queue.py\n")
            self.assertEqual(shadow.capsule_from_brief(path), {
                "goal": "Project title", "paths": ["src/queue.py"],
                "acceptance_criteria": ["Once"]})

    def test_no_title_or_goal_still_requires_a_capsule(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.md"
            path.write_text("Unscoped private background\n## Contract\n- Deliver once\n")
            with self.assertRaises(shadow.ShadowError):
                shadow.capsule_from_brief(path)

    def test_schema_rejects_unknown_keys_bool_nan_and_wrong_sum(self):
        for value in ({**ANSWER, "model": "winner"}, {**ANSWER, "weights": {"terminal": True}},
                      {**ANSWER, "weights": {"terminal": float("nan")}},
                      {**ANSWER, "weights": {"unknown": 1}},
                      {**ANSWER, "weights": {"terminal": 0.8}},
                      {**ANSWER, "taxonomy_version": "1.0"},
                      {**ANSWER, "status": "insufficient_context"}):
            with self.assertRaises(shadow.ShadowError):
                shadow.validate_classification(value)
        normalized = shadow.validate_classification({**ANSWER, "weights": {"terminal": 0.9999}})
        self.assertEqual(normalized["weights"], {"terminal": 1.0})
        with self.assertRaises(shadow.ShadowError):
            shadow.strict_json('{"weights":{},"weights":{}}')

    def test_command_is_subscription_safe_and_tools_disabled(self):
        command = shadow.classifier_command(CONFIG)
        self.assertIn("--safe-mode", command)
        self.assertNotIn("--bare", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--model") + 1], CONFIG["model"])
        self.assertNotIn("gpt-", shadow.SYSTEM_PROMPT)
        self.assertNotIn("claude-", shadow.SYSTEM_PROMPT)


class ClassifierProcessTest(unittest.TestCase):
    def run_fake(self, payload, config=None):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake.py"
            script.write_text("import sys,time\n" + payload)
            with patch.object(shadow, "classifier_command", return_value=[sys.executable, str(script)]):
                return shadow.invoke_classifier(config or CONFIG, {"goal": "sample"})

    def test_fresh_call_checks_model_and_captures_usage(self):
        result = {"type": "result", "subtype": "success", "is_error": False,
                  "structured_output": ANSWER, "modelUsage": {CONFIG["model"]: {}},
                  "usage": {"input_tokens": 123, "output_tokens": 20}, "total_cost_usd": 0.01}
        got = self.run_fake("print(" + repr(json.dumps(result)) + ")\n")
        self.assertEqual(got["resolved_model"], CONFIG["model"])
        self.assertEqual(got["usage"]["input_tokens"], 123)
        result["modelUsage"] = {"different-model": {}}
        with self.assertRaisesRegex(shadow.ShadowError, "identity mismatch"):
            self.run_fake("print(" + repr(json.dumps(result)) + ")\n")

    def test_timeout_nonjson_and_process_failure(self):
        with self.assertRaisesRegex(shadow.ShadowError, "timeout"):
            self.run_fake("time.sleep(5)\n", {**CONFIG, "timeout_seconds": 0.05})
        with self.assertRaises(ValueError):
            self.run_fake("print('not json')\n")
        with self.assertRaisesRegex(shadow.ShadowError, "process failed"):
            self.run_fake("sys.exit(4)\n")

    def test_failed_budget_identity_schema_and_tool_cases_keep_reported_usage(self):
        base = {"type": "result", "subtype": "success", "is_error": False,
                "structured_output": ANSWER, "modelUsage": {CONFIG["model"]: {}},
                "usage": {"input_tokens": 123, "output_tokens": 20}, "total_cost_usd": .027}
        cases = [({**base, "subtype": "error_max_budget_usd", "is_error": True}, 1, []),
                 ({**base, "modelUsage": {"other-model": {}}}, 0, []),
                 ({**base, "structured_output": {"wrong": "schema"}}, 0, []),
                 (base, 0, [{"type": "assistant", "message": {"model": CONFIG["model"],
                            "content": [{"type": "tool_use", "name": "Bash"}]}}])]
        for result, code, before in cases:
            payload = "\n".join("print(" + repr(json.dumps(row)) + ")" for row in [*before, result])
            payload += f"\nsys.exit({code})\n"
            with self.subTest(code=code, subtype=result['subtype']):
                with self.assertRaises(shadow.ClassifierFailure) as raised:
                    self.run_fake(payload)
                self.assertEqual(raised.exception.telemetry["api_equivalent_usd"], .027)
                self.assertEqual(raised.exception.telemetry["usage"]["output_tokens"], 20)

    def test_timeout_without_result_has_unknown_cost_not_zero(self):
        with self.assertRaises(shadow.ClassifierFailure) as raised:
            self.run_fake("print('partial',flush=True)\ntime.sleep(5)\n",
                          {**CONFIG, "timeout_seconds": .05})
        self.assertIsNone(raised.exception.telemetry["api_equivalent_usd"])
        self.assertFalse(raised.exception.telemetry["response_complete"])


if __name__ == "__main__":
    unittest.main()
