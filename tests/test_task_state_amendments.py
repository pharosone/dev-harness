"""Safe frozen-test amendments, exercised through the CLI in real worktrees."""
import hashlib
import json
import subprocess
import sys
import unittest

import test_task_state as baseline


class FreezeAmendmentTest(unittest.TestCase):
    setUp = baseline.TaskStateTest.setUp
    git = baseline.TaskStateTest.git
    run_state = baseline.TaskStateTest.run_state
    ok = baseline.TaskStateTest.ok
    red_log = baseline.TaskStateTest.red_log
    freeze = baseline.TaskStateTest.freeze

    def prepare(self, source, *, red=None, policy=None):
        self.name = "tests/test one.py"
        self.test_file = self.worktree / self.name
        self.test_file.write_text(source)
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.red = self.red_log(red or "FAIL: test_one\nAssertionError: 1 != 2\n")
        args = ["feature", "01", self.name]
        if policy:
            brief = self.worktree / "brief.md"
            brief.write_text(policy)
            args.extend(["--brief", "brief.md"])
        self.ok(self.freeze(*args, cwd=self.worktree, red=self.red))
        self.manifest = self.root / ".harness/tasks/feature/units/01-tests.json"
        self.original = self.manifest.read_bytes()

    def amend(self, source, **kwargs):
        self.test_file.write_text(source)
        return self.freeze("feature", "01", self.name, cwd=self.worktree,
                           red=kwargs.pop("red", self.red), **kwargs)

    def accepted(self, result, reason):
        self.ok(result)
        self.ok(self.run_state("verify", "feature", "01", cwd=self.worktree))
        data = json.loads(self.manifest.read_text())
        record = data["amendments"][-1]
        self.assertEqual(record["reason"], reason)
        archive = self.manifest.parent / record["archive"]
        self.assertEqual(archive.read_bytes(), self.original)
        self.assertEqual(record["archive_sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertIn(reason, result.stderr)

    def rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.manifest.read_bytes(), self.original)
        self.assertEqual(list(self.manifest.parent.glob("*-archive-*.json")), [])

    def test_formatting_and_unused_import_removal_refreeze_across_worktrees(self):
        self.prepare("import os, sys\n\ndef test_one():\n    assert sys.version_info.major >= 3\n")
        self.accepted(self.amend("import sys\n\n# formatted\ndef test_one():\n    assert (\n        sys.version_info.major >= 3\n    )\n"), "ast-equivalent")
        # Verification uses the lane's files, not the primary checkout's files.
        self.assertNotEqual(self.run_state("verify", "feature", "01").returncode, 0)

    def test_imported_test_class_is_not_removed_as_unused(self):
        old = "from shared_contracts import SharedContract\n\ndef test_one():\n    assert True\n"
        self.prepare(old)
        self.rejected(self.amend(old.replace("from shared_contracts import SharedContract\n", "")))

    def test_unknown_imported_bindings_require_approval(self):
        for name in ("contract", "fixture", "SharedContract"):
            with self.subTest(name=name):
                old = f"from custom_helpers import {name}\n\ndef test_one():\n    assert True\n"
                self.prepare(old)
                self.rejected(self.amend("def test_one():\n    assert True\n"))
                self.manifest.unlink()

    def test_unknown_plain_import_registration_is_preserved(self):
        old = "import shared_contracts\n\ndef test_one():\n    assert True\n"
        self.prepare(old)
        self.rejected(self.amend("def test_one():\n    assert True\n"))

    def test_imported_contract_cannot_be_removed_on_setup_error(self):
        old = "from shared_contracts import SharedContract\n\ndef setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="NameError: name 'missing_api' is not defined\n")
        self.rejected(self.amend(old.replace("from shared_contracts import SharedContract\n", "")))

    def test_setup_import_cannot_shadow_a_collected_contract(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="NameError: name 'missing_api' is not defined\n")
        self.rejected(self.amend(old + "\nfrom pathlib import Path as test_one\n"))

    def test_setup_can_add_a_missing_fixture_import(self):
        old = "def setup_function():\n    fixture = MissingFixture()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="NameError: name 'MissingFixture' is not defined\n")
        self.accepted(self.amend("from custom_helpers import MissingFixture\n" + old), "setup-only")

    def test_known_noncollection_stdlib_binding_can_be_unused(self):
        old = "from pathlib import Path\n\ndef test_one():\n    assert True\n"
        self.prepare(old)
        self.accepted(self.amend("def test_one():\n    assert True\n"), "ast-equivalent")

    def test_setup_cannot_introduce_skip_or_xfail(self):
        old = "import unittest\n\nclass TestOne(unittest.TestCase):\n    def setUp(self):\n        self.fixture = missing_api()\n\n    def test_one(self):\n        self.assertEqual(self.fixture, 42)\n"
        self.prepare(old, red="NameError: name 'missing_api' is not defined\n")
        for control in ("self.skipTest('fixture unavailable')", "pytest.skip('unavailable')", "pytest.xfail('later')", "raise unittest.SkipTest('unavailable')", "pytest.importorskip('fixture')"):
            with self.subTest(control=control):
                self.rejected(self.amend(old.replace("self.fixture = missing_api()", control)))

    def test_setup_skip_aliases_and_reflection_are_protected(self):
        old = "import unittest\n\nclass TestOne(unittest.TestCase):\n    def setUp(self):\n        self.fixture = missing_api()\n\n    def test_one(self):\n        self.assertEqual(self.fixture, 42)\n"
        self.prepare(old, red="NameError: name 'missing_api' is not defined\n")
        changes = (
            "from unittest import SkipTest as MissingFixture\n" + old.replace("self.fixture = missing_api()", "raise MissingFixture('unavailable')"),
            "from pytest import skip as finish\n" + old.replace("self.fixture = missing_api()", "finish('unavailable')"),
            old.replace("self.fixture = missing_api()", "getattr(self, 'skipTest')('unavailable')"),
        )
        for changed in changes:
            with self.subTest(changed=changed):
                self.rejected(self.amend(changed))

    def test_existing_skip_control_flow_is_protected(self):
        old = "import unittest\n\nclass TestOne(unittest.TestCase):\n    def setUp(self):\n        if False:\n            self.skipTest('unavailable')\n        self.fixture = missing_api()\n\n    def test_one(self):\n        self.assertEqual(self.fixture, 42)\n"
        self.prepare(old, red="NameError: name 'missing_api' is not defined\n")
        self.rejected(self.amend(old.replace("if False:", "if True:")))

    def test_zero_tests_and_all_skipped_logs_cannot_record_green(self):
        self.prepare("def test_one():\n    assert True\n")
        for text in ("Ran 0 tests in 0.1s\nOK\n", "Ran 1 test in 0.1s\nOK (skipped=1)\n", "OK\n", "Ran 1 test in 0.1s\nOK (expected failures=1)\n"):
            with self.subTest(text=text):
                log = self.red_log(text, name="green.log")
                self.rejected(self.run_state("green", "feature", "01", "--green-log", str(log), cwd=self.worktree))

    def test_used_import_or_new_import_or_assertion_change_still_needs_approval(self):
        source = "import sys\n\ndef test_one():\n    assert sys.version_info.major >= 3\n"
        self.prepare(source)
        changes = (source.replace("import sys", ""), "import os\n" + source,
                   source.replace(">= 3", ">= 2"))
        for changed in changes:
            with self.subTest(source=changed):
                self.rejected(self.amend(changed))

    def test_qualified_setup_exception_with_digits_is_supported(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red='Traceback (most recent call last):\n  File "tests/test one.py", line 2, in setup_function\npsycopg2.errors.UndefinedColumn: missing fixture column\n')
        self.accepted(self.amend(old.replace("missing_api", "correct_api")), "setup-only")

    def test_setup_only_before_first_green_keeps_assertions_byte_identical(self):
        old = "import unittest\n\nclass TestOne(unittest.TestCase):\n    def setUp(self):\n        self.fixture = Fake(wrong=True)\n\n    def test_one(self):\n        self.assertEqual(self.fixture.value, 42)  # contract\n"
        self.prepare(old, red="ERROR: test_one\nTraceback (most recent call last):\n  in setUp\nTypeError: Fake got unexpected keyword 'wrong'\n")
        self.accepted(self.amend(old.replace("wrong=True", "correct=True")), "setup-only")

    def test_setup_rejects_changed_assertion_bytes_or_non_setup_code(self):
        old = "import unittest\n\nclass TestOne(unittest.TestCase):\n    def setUp(self):\n        self.fixture = Fake(wrong=True)\n\n    def test_one(self):\n        self.assertEqual(self.fixture.value, 42)\n"
        self.prepare(old, red="ERROR: test_one\n  in setUp\nTypeError: Fake got unexpected keyword 'wrong'\n")
        fixed = old.replace("wrong=True", "correct=True")
        for changed in (fixed.replace(", 42)", ",42)"), fixed.replace(", 42)", ", 41)"),
                        fixed.replace("    def test_one(self):", "    def test_other(self):")):
            with self.subTest(source=changed):
                self.rejected(self.amend(changed))

    def test_setup_rejects_assertion_failure_mixed_log_and_tampered_red_evidence(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="ImportError: missing fixture API\nAssertionError: expected 42\n")
        self.rejected(self.amend(old.replace("missing_api", "correct_api")))
        self.red.write_text("ImportError: missing fixture API\n")
        self.rejected(self.amend(old.replace("missing_api", "correct_api")))

    def test_setup_rejects_unrelated_attribute_and_type_errors(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="TypeError: application handler has wrong argument\n")
        self.rejected(self.amend(old.replace("missing_api", "correct_api")))

    def test_setup_context_must_not_hide_an_application_runtime_error(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red='ERROR: test_one\nTraceback (most recent call last):\n'
                     '  File "tests/test_one.py", line 2, in setup_function\n'
                     '    fixture = missing_api()\n'
                     '  File "app.py", line 20, in handle_request\n'
                     '    response = service()\n'
                     'TypeError: application handler has wrong argument\n')
        self.rejected(self.amend(old.replace("missing_api", "correct_api")))

    def test_setup_only_supports_pytest_fixture_and_each_setup_error_kind(self):
        old = "import pytest\n\n@pytest.fixture\ndef record():\n    return Fake(wrong=True)\n\ndef test_one(record):\n    assert record.value == 42\n"
        for index, error in enumerate(("ImportError: missing API", "NameError: fixture_name missing",
                                       "psycopg.errors.UndefinedColumn: missing column",
                                       "AttributeError: Fake has no attribute old_api")):
            unit = f"kind{index}"
            with self.subTest(error=error):
                self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
                name = "tests/test one.py"
                test = self.worktree / name
                test.write_text(old)
                red = self.red_log("Traceback (most recent call last):\n" + error + "\n", name=f"{unit}.log")
                self.ok(self.freeze("feature", unit, name, cwd=self.worktree, red=red))
                test.write_text(old.replace("wrong=True", "correct=True"))
                self.ok(self.freeze("feature", unit, name, cwd=self.worktree, red=red))
                self.ok(self.run_state("verify", "feature", unit, cwd=self.worktree))

    def test_pytest_error_output_without_traceback_is_setup_evidence(self):
        old = "import pytest\n\n@pytest.fixture\ndef record():\n    return Fake(wrong=True)\n\ndef test_one(record):\n    assert record.value == 42\n"
        self.prepare(old, red="________________ ERROR at setup of test_one ________________\n"
                     "tests/test_one.py:5: in record\n    return Fake(wrong=True)\n"
                     "E   TypeError: Fake got unexpected keyword 'wrong'\n"
                     "ERROR tests/test_one.py::test_one - TypeError: Fake\n1 error in 0.01s\n")
        self.accepted(self.amend(old.replace("wrong=True", "correct=True")), "setup-only")

    @unittest.skipIf(baseline.fcntl is None, "automatic refreezes require file locking")
    def test_concurrent_refreezes_keep_exactly_one_archive(self):
        self.prepare("assert True\n")
        self.test_file.write_text("# lint\nassert True\n")
        command = [sys.executable, str(baseline.SCRIPT), "freeze", "feature", "01", self.name,
                   "--red-log", str(self.red)]
        writers = [subprocess.Popen(command, cwd=self.worktree, env=self.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for _ in range(2)]
        for writer in writers:
            self.addCleanup(writer.kill)
        for writer in writers:
            writer.communicate(timeout=30)
        self.assertEqual(sorted(writer.returncode for writer in writers), [0, 1])
        archives = list(self.manifest.parent.glob("*-archive-*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), self.original)
        self.ok(self.run_state("verify", "feature", "01", cwd=self.worktree))

    def test_inline_setup_in_test_body_remains_approval_only(self):
        old = "def test_one():\n    fixture = Fake(wrong=True)\n    assert fixture.value == 42\n"
        self.prepare(old, red="TypeError: Fake got unexpected keyword 'wrong'\n")
        self.rejected(self.amend(old.replace("wrong=True", "correct=True")))

    def test_named_fake_missing_method_is_setup_only(self):
        old = ("class FakeDB:\n    def query(self):\n        return 42\n\n"
               "def test_one():\n    fixture = FakeDB()\n    assert fixture.execute() == 42\n")
        self.prepare(old, red="AttributeError: 'FakeDB' object has no attribute 'execute'\n")
        fixed = old.replace("        return 42\n", "        return 42\n\n    def execute(self):\n        return self.query()\n")
        self.accepted(self.amend(fixed), "setup-only")

    def test_named_stub_constructor_frame_is_setup_only(self):
        old = ("class StubDB:\n    def __init__(self):\n        self.rows = list(None)\n\n"
               "def test_one():\n    fixture = StubDB()\n    assert fixture.rows == []\n")
        self.prepare(old, red='ERROR: test_one\nTraceback (most recent call last):\n'
                     '  File "tests/test one.py", line 3, in __init__\n'
                     '    self.rows = list(None)\nTypeError: NoneType is not iterable\n')
        self.accepted(self.amend(old.replace("list(None)", "list()")), "setup-only")

    def test_named_fake_factory_is_setup_only(self):
        old = "def FakeRecord():\n    return missing_api()\n\ndef test_one():\n    assert FakeRecord().value == 42\n"
        self.prepare(old, red='Traceback (most recent call last):\n'
                     '  File "tests/test one.py", line 2, in FakeRecord\n'
                     '    return missing_api()\nNameError: missing_api is not defined\n')
        self.accepted(self.amend(old.replace("return missing_api()", "return correct_api()")), "setup-only")

    def test_other_fake_or_real_class_or_assertion_cannot_change(self):
        old = ("class FakeDB:\n    pass\n\nclass FakeOther:\n    pass\n\nclass Database:\n    pass\n\n"
               "def test_one():\n    assert FakeDB().execute() == 42\n")
        self.prepare(old, red="AttributeError: 'FakeDB' object has no attribute 'execute'\n")
        for changed in (old.replace("class FakeOther:\n    pass", "class FakeOther:\n    value = 42"),
                        old.replace("class Database:\n    pass", "class Database:\n    value = 42"),
                        old.replace("== 42", "== 41")):
            with self.subTest(source=changed):
                self.rejected(self.amend(changed))

    def test_fake_setup_error_does_not_allow_changes_after_green(self):
        old = "class FakeDB:\n    pass\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="AttributeError: 'FakeDB' object has no attribute 'execute'\n")
        green = self.red_log("1 passed in 0.1s\n", name="green.log")
        self.ok(self.run_state("green", "feature", "01", "--green-log", str(green), cwd=self.worktree))
        self.original = self.manifest.read_bytes()
        self.rejected(self.amend(old.replace("    pass", "    execute = lambda self: 42")))

    def test_fake_constructor_safety_rejects_external_runtime_frames(self):
        old = "class FakeDB:\n    def __init__(self):\n        self.rows = list(None)\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red='Traceback (most recent call last):\n'
                     '  File "app.py", line 3, in handle_request\n'
                     'TypeError: FakeDB handler returned an invalid response\n')
        self.rejected(self.amend(old.replace("list(None)", "list()")))

    def test_fake_constructor_safety_rejects_wrong_file_traceback(self):
        old = "class FakeDB:\n    def __init__(self):\n        self.rows = list(None)\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red='Traceback (most recent call last):\n'
                     '  File "app.py", line 3, in __init__\n'
                     'TypeError: NoneType is not iterable\n')
        self.rejected(self.amend(old.replace("list(None)", "list()")))

    def test_fake_constructor_safety_preserves_assertions_inside_fake(self):
        old = "class FakeDB:\n    def __init__(self):\n        self.rows = list(None)\n        assert self.rows == []\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red='Traceback (most recent call last):\n'
                     '  File "tests/test one.py", line 3, in __init__\n'
                     'TypeError: NoneType is not iterable\n')
        changed = old.replace("list(None)", "list()").replace("        assert self.rows == []\n", "")
        self.rejected(self.amend(changed))

    def test_assertions_inside_setup_cannot_be_removed(self):
        old = "def setup_function():\n    fixture = missing_api()\n    assert fixture\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="ImportError: missing fixture API\n")
        self.rejected(self.amend(old.replace("missing_api", "correct_api").replace("    assert fixture\n", "")))

    def test_assertion_context_manager_body_inside_setup_is_byte_identical(self):
        old = ("import pytest\n\ndef setup_function():\n    fixture = missing_api()\n"
               "    with pytest.raises(ValueError):\n        validate_fixture(fixture)\n"
               "\ndef test_one():\n    assert True\n")
        self.prepare(old, red="ImportError: missing fixture API\n")
        changed = old.replace("missing_api", "correct_api").replace("validate_fixture(fixture)", "int('bad')")
        self.rejected(self.amend(changed))

    def test_malformed_snapshot_or_policy_is_a_clean_rejection(self):
        self.prepare("assert True\n")
        pristine = self.original
        for field, value in (("sources", {self.name: 1}),
                             ("append_only", {"targets": "invalid"}),
                             ("amendments", {})):
            with self.subTest(field=field):
                data = json.loads(pristine)
                data[field] = value
                self.manifest.write_text(json.dumps(data))
                self.original = self.manifest.read_bytes()
                result = self.amend("# lint\nassert True\n")
                self.rejected(result)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)

    def test_multiline_assertions_comments_and_line_endings_are_protected(self):
        old = ("def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n"
               "    assert (\n        1 == 1  # contract\n    )\n")
        self.prepare(old, red="ImportError: missing fixture API\n")
        fixed = old.replace("missing_api", "correct_api")
        for changed in (fixed.replace("# contract", "# edited"), fixed.replace("\n", "\r\n")):
            with self.subTest(source=changed):
                self.rejected(self.amend(changed))

    def test_old_manifest_without_source_snapshots_remains_approval_only(self):
        self.prepare("assert True\n")
        data = json.loads(self.original)
        del data["sources"]
        self.manifest.write_text(json.dumps(data))
        self.original = self.manifest.read_bytes()
        self.rejected(self.amend("# formatted\nassert True\n"))

    def test_new_nonliteral_or_unfrozen_brief_targets_cannot_authorize_changes(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        name = "tests/test one.py"
        (self.worktree / name).write_text("EXPECTED = []\n")
        (self.worktree / "brief.md").write_text("task-state append-only: spec.md:EXPECTED\n")
        result = self.freeze("feature", "01", name, "--brief", "brief.md", cwd=self.worktree)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".harness/tasks/feature/units/01-tests.json").exists())

    def test_first_green_blocks_setup_repair_but_allows_formatting(self):
        old = "def setup_function():\n    fixture = missing_api()\n\ndef test_one():\n    assert True\n"
        self.prepare(old, red="ImportError: missing fixture API\n")
        green = self.red_log("Ran 1 test in 0.01s\n\nOK\n", name="green.log")
        self.ok(self.run_state("green", "feature", "01", "--green-log", str(green), cwd=self.worktree))
        self.original = self.manifest.read_bytes()
        self.rejected(self.amend(old.replace("missing_api", "correct_api")))
        self.accepted(self.amend("# lint\n" + old), "ast-equivalent")
        self.assertIn("first_green", json.loads(self.manifest.read_text()))

    def test_green_requires_passing_log_and_verified_tests(self):
        old = "assert True\n"
        self.prepare(old)
        for text in ("not a test result\n", "OK\nAssertionError: bad\n", "0 passed\n", "0 passed, 1 failed\n"):
            with self.subTest(log=text):
                log = self.red_log(text, name="green.log")
                self.rejected(self.run_state("green", "feature", "01", "--green-log", str(log), cwd=self.worktree))
        self.test_file.write_text("assert False\n")
        green = self.red_log("1 passed in 0.1s\n", name="green.log")
        self.rejected(self.run_state("green", "feature", "01", "--green-log", str(green), cwd=self.worktree))

    def test_brief_authorizes_append_only_named_list_tuple_and_set_literals(self):
        source = "EXPECTED = ['a']\nTOOLS = ('b',)\nNAMES = {'c'}\n\ndef test_one():\n    assert EXPECTED == ['a']\n"
        self.prepare(source, policy="task-state append-only: tests/test one.py:EXPECTED\ntask-state append-only: tests/test one.py:TOOLS\ntask-state append-only: tests/test one.py:NAMES\n")
        # Authorization was pinned at freeze; no mutable brief is consulted later.
        (self.worktree / "brief.md").write_text("changed after freezing\n")
        self.accepted(self.amend(source.replace("['a']", "['a', 'd']", 1)
                                .replace("('b',)", "('b', 'e')").replace("{'c'}", "{'c', 'f'}")), "append-only")

    def test_append_policy_cannot_be_added_after_freeze(self):
        source = "EXPECTED = ['a']\nassert EXPECTED\n"
        self.prepare(source)
        (self.worktree / "brief.md").write_text("task-state append-only: tests/test one.py:EXPECTED\n")
        self.test_file.write_text(source.replace("['a']", "['a', 'b']"))
        result = self.freeze("feature", "01", self.name, "--brief", "brief.md", cwd=self.worktree, red=self.red)
        self.rejected(result)

    def test_append_only_rejects_deletion_reordering_replacement_other_edits_and_calls(self):
        source = "EXPECTED = ['a', 'b']\nOTHER = [1]\nassert EXPECTED\n"
        self.prepare(source, policy="task-state append-only: tests/test one.py:EXPECTED\n")
        for changed in (source.replace("['a', 'b']", "['a']"), source.replace("['a', 'b']", "['b', 'a', 'c']"),
                        source.replace("['a', 'b']", "['a', 'x', 'c']"), source.replace("[1]", "[1, 2]"),
                        source.replace("['a', 'b']", "['a', 'b', compute()]"),
                        source.replace("assert EXPECTED", "assert True")):
            with self.subTest(source=changed):
                self.rejected(self.amend(changed))

    def test_append_policy_supports_qualified_local_names(self):
        source = "def test_one():\n    expected = ['a']\n    assert expected\n"
        self.prepare(source, policy="task-state append-only: tests/test one.py:test_one.expected\n")
        self.accepted(self.amend(source.replace("['a']", "['a', 'b']")), "append-only")

    def test_manifest_file_set_cannot_change_and_missing_baseline_fails_closed(self):
        self.prepare("assert True\n")
        self.rejected(self.freeze("feature", "01", self.name, "spec.md", cwd=self.worktree, red=self.red))
        data = json.loads(self.original)
        data["files"][self.name] = "f" * 40
        self.manifest.write_text(json.dumps(data))
        self.original = self.manifest.read_bytes()
        self.rejected(self.amend("# changed\nassert True\n"))

    def test_initial_policy_rejects_missing_ambiguous_or_nonliteral_targets(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        name = "tests/test one.py"
        for source, target in (("EXPECTED = make()\n", "EXPECTED"), ("EXPECTED = []\n", "MISSING"),
                               ("EXPECTED = []\nEXPECTED = []\n", "EXPECTED")):
            with self.subTest(source=source):
                (self.worktree / name).write_text(source)
                (self.worktree / "brief.md").write_text(f"task-state append-only: {name}:{target}\n")
                result = self.freeze("feature", "01", name, "--brief", "brief.md", cwd=self.worktree)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / ".harness/tasks/feature/units/01-tests.json").exists())


if __name__ == "__main__":
    unittest.main()
