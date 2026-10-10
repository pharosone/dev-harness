"""CLI dispatch integration, with isolated usage stores and fake worker executables."""
import json
import os
from pathlib import Path
import unittest

import test_balance as balance_fixture
import test_lanes_launch as lane_fixture


WEIGHTS = json.dumps({"terminal": .6, "tool_workflow": .25, "review": .15})
PROXY = {"mode": "shadow", "classifier": {"enabled": False},
         "estimates": {"proxy_mode": "aa_output_decode", "initial_review_cu": 10}}


class BalanceShadowIntegrationTest(balance_fixture.BalanceTest):
    def test_shadow_off_preserves_exact_output_and_event_contract(self):
        (self.project / "router.json").write_text(json.dumps({"mode": "off"}))
        result = self.pick("--tier", "3", "--router-weights", WEIGHTS,
                           profile="critical-review")
        self.assertNotIn("shadow", result)
        self.assertEqual([event["type"] for event in self.events()], ["balance.pick"])
        self.assertFalse((self.run_dir / "router-cache").exists())

    def test_shadow_recommendation_does_not_replace_static_pick(self):
        baseline = self.pick(profile="critical-review")
        (self.project / "router.json").write_text(json.dumps(PROXY))
        result = self.pick("--tier", "3", "--router-weights", WEIGHTS,
                           profile="critical-review")
        for key in ("runtime", "model", "effort"):
            self.assertEqual(result[key], baseline[key])
            self.assertEqual(result["shadow"]["actual"][key], baseline[key])
        self.assertIsNotNone(result["shadow"]["recommendation"])
        self.assertEqual(result["shadow"]["mode"], "shadow")
        self.assertEqual(self.events()[-1]["type"], "router.shadow")
        self.assertEqual(self.events()[-1]["decision_id"], result["shadow"]["decision_id"])
        self.assertTrue(result["shadow"]["assumptions"])

    def test_no_estimates_keeps_quality_evidence_but_has_no_cost_winner(self):
        (self.project / "router.json").write_text(json.dumps({"mode": "shadow"}))
        result = self.pick("--tier", "3", "--router-weights", WEIGHTS,
                           profile="critical-review")
        self.assertEqual(result["shadow"]["status"], "unscored")
        self.assertIsNone(result["shadow"]["recommendation"])
        self.assertTrue(result["shadow"]["candidates"])
        self.assertIn(result["runtime"], ("claude", "codex"))

    def test_disabled_classifier_and_bad_config_are_logged_skips(self):
        (self.project / "router.json").write_text(json.dumps({"mode": "shadow"}))
        result = self.pick("--tier", "1")
        self.assertEqual(result["shadow"]["status"], "skipped")
        self.assertIn("classifier disabled", result["shadow"]["reason"])
        (self.project / "router.json").write_text(json.dumps({"mode": "active"}))
        result = self.pick()
        self.assertEqual(result["shadow"]["status"], "skipped")
        self.assertIn(result["runtime"], ("claude", "codex"))

    def test_excluded_vendor_is_never_recommended(self):
        (self.project / "router.json").write_text(json.dumps(PROXY))
        result = self.pick("--tier", "3", "--router-weights", WEIGHTS,
                           "--not-runtime", "claude", profile="critical-review")
        self.assertEqual(result["runtime"], "codex")
        self.assertEqual(result["shadow"]["recommendation"]["runtime"], "codex")


class LaneShadowIntegrationTest(unittest.TestCase):
    # Reuse only the fake-process fixture helpers, not its complete inherited suite.
    cleanup = lane_fixture.LaunchTest.cleanup
    git = lane_fixture.LaunchTest.git
    lanes = lane_fixture.LaunchTest.lanes
    events = lane_fixture.LaunchTest.events
    calls = lane_fixture.LaunchTest.calls
    wait = lane_fixture.LaunchTest.wait
    state_file = lane_fixture.LaunchTest.state_file

    def setUp(self):
        lane_fixture.LaunchTest.setUp(self)
        self.overlay = self.repo / "harness/project"
        self.overlay.mkdir(parents=True)
        self.env.update(HARNESS_PROJECT=str(self.overlay), CODEX_HOME=str(self.temp / "codex"),
                        XDG_STATE_HOME=str(self.temp / "state"))
        self.env.pop("HARNESS_ORCHESTRATOR", None)

    def configure(self, value=PROXY):
        (self.overlay / "router.json").write_text(json.dumps(value))

    def start_shadow(self, *extra):
        result = self.lanes("start", "--task", "t1", "--work", "U1", "--role", "implementer",
            "--runtime", "codex", "--model", "gpt-6-astra", "--effort", "medium",
            "--brief", str(self.brief), "--report", str(self.temp / "report.md"),
            "--checkout", str(self.repo), "--launcher", "headless", "--tier", "3",
            "--profile", "critical-review", "--router-weights", WEIGHTS, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.started.append(value["dispatch"])
        self.wait(value["dispatch"])
        return value

    def test_start_actual_worker_and_calibration_links(self):
        self.configure()
        result = self.start_shadow()
        state = self.state_file(result["dispatch"])
        self.assertEqual((state["runtime"], state["model"], state["effort"]),
                         ("codex", "gpt-6-astra", "medium"))
        start = next(row for row in self.events() if row["type"] == "dispatch.start")
        event = next(row for row in self.events() if row["type"] == "router.shadow")
        self.assertEqual(start["attempt_id"], result["dispatch"])
        self.assertEqual(start["router_decision_id"], event["decision_id"])
        self.assertEqual(start["selected_tuple"], event["actual"])
        self.assertEqual(start["policy_hash"], event["policy_hash"])
        self.assertEqual(start["root_attempt_id"], result["dispatch"])
        self.assertTrue(event["recommendation"])
        self.assertNotEqual(event["recommendation"]["effort"], "medium")
        self.assertEqual(len(self.calls()), 1, "manual weights must not launch a classifier")
        call = self.calls()[0]
        self.assertIn("gpt-6-astra", json.dumps(call))

    def test_start_off_records_no_new_calibration_fields(self):
        result = self.start_shadow()
        self.assertNotIn("shadow", result)
        start = next(row for row in self.events() if row["type"] == "dispatch.start")
        self.assertNotIn("attempt_id", start)
        self.assertNotIn("policy_hash", start)
        self.assertFalse(any(row["type"] == "router.shadow" for row in self.events()))

    def test_decide_is_advisory_and_logs_when_task_is_given(self):
        self.configure()
        result = self.lanes("decide", "--role", "implementer", "--runtime", "codex",
            "--orchestrator", "codex", "--minutes", "5", "--task", "t1", "--tier", "2",
            "--profile", "implementation", "--router-weights", '{"terminal":1}')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["launcher"], "subagent")
        self.assertIn("shadow", value)
        self.assertEqual(self.events()[-1]["type"], "router.shadow")
        self.assertEqual(self.calls(), [])

    def test_start_retains_explicit_parent_and_root_without_inventing_acceptance(self):
        self.configure()
        first = self.start_shadow()
        second = self.start_shadow("--parent-attempt-id", first["dispatch"])
        state = self.state_file(second["dispatch"])
        self.assertEqual(state["parent_attempt_id"], first["dispatch"])
        self.assertEqual(state["root_attempt_id"], first["dispatch"])
        self.assertFalse(any(row["type"] == "router.label" for row in self.events()))


if __name__ == "__main__":
    unittest.main()
