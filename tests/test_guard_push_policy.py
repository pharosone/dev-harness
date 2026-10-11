"""Owner push opt-in changes only verified, ordinary Claude feature pushes."""
import json
import unittest
from test_guard import GuardHarness


class PushPolicyTest(GuardHarness, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.git('remote', 'add', 'origin', 'https://example.invalid/test.git')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.git('checkout', '-qb', 'feature/push')
        self.git('config', 'branch.feature/push.remote', 'origin')
        self.git('config', 'branch.feature/push.merge', 'refs/heads/feature/push')
        self.git('config', 'push.default', 'simple')

    def policy(self, value='allow'):
        return self.use_overlay(json.dumps({'guard': {'push': value}}))

    def test_default_and_explicit_ask_preserve_core_policy(self):
        self.assert_guarded(self.bash('git push origin feature/push'), 'claude', 'push')
        self.policy('ask')
        self.assert_guarded(self.bash('git push'), 'claude', 'push')

    def test_allow_handles_explicit_and_configured_feature_destinations(self):
        self.policy()
        for cmd in ('git push origin feature/push', 'git push -u origin HEAD',
                    'git push origin HEAD:refs/heads/feature/push', 'git push',
                    'git push origin', 'git subtree push --prefix=sdk origin feature/push',
                    'git subtree -P sdk push origin feature/push'):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.bash(cmd))

    def test_force_deletion_mirror_default_and_dynamic_forms_still_ask(self):
        self.policy()
        commands = [
            'git push --force origin feature/push', 'git push -uf origin feature/push',
            'git push --force-with-lease origin feature/push',
            'git push --force-with-lease=feature/push origin feature/push',
            'git push --for origin feature/push', 'git push origin +HEAD:feature/push',
            'git push --mirror origin', 'git push --mir origin',
            'git push --delete origin feature/push', 'git push -d origin feature/push',
            'git push origin :feature/push', 'git push origin main',
            'git push origin HEAD:refs/heads/master', 'git push origin HEAD:HEAD',
            'git subtree push --prefix=sdk origin main', 'git push --all origin',
            'git push --prune origin', 'git push origin :',
            'git push origin refs/heads/*:refs/heads/*', 'git push origin $BRANCH',
            'git $(echo push) origin feature/push', 'git -c alias.p=push p origin feature/push',
            "git -c alias.p='!git push origin feature/push' p",
            'echo main | xargs git push origin',
            'git branch -M main && git push origin HEAD',
            'git -c remote.origin.mirror=true push origin feature/push',
            'git send-pack origin HEAD:feature/push',
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd):
                self.assert_guarded(self.bash(cmd), 'claude', 'push')
        self.git('update-ref', 'refs/remotes/origin/trunk', 'HEAD')
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        self.assert_guarded(self.bash('git push origin HEAD:trunk'), 'claude', 'push')

    def test_implicit_routing_cannot_hide_protected_or_forced_refs(self):
        self.policy()
        for key, value in [('remote.origin.push', 'HEAD:main'),
                           ('remote.origin.push', '+HEAD:feature/push'),
                           ('remote.origin.push', ':feature/push'),
                           ('remote.origin.mirror', 'true'), ('push.default', 'matching')]:
            with self.subTest(key=key, value=value):
                self.git('config', key, value)
                self.assert_guarded(self.bash('git push'), 'claude', 'push')
                self.git('config', '--unset-all', key)
        self.git('config', 'remote.origin.push', 'HEAD:feature/push')
        self.assertIsNone(self.bash('git push'))
        self.git('config', '--unset-all', 'remote.origin.push')
        self.git('checkout', 'main')
        self.assert_guarded(self.bash('git push origin HEAD'), 'claude', 'push')

    def test_configured_source_mapping_and_head_synonyms_stay_protected(self):
        self.policy()
        self.git('config', 'remote.origin.push', 'refs/heads/feature/push:refs/heads/main')
        self.assert_guarded(self.bash('git push origin feature/push'), 'claude', 'push')
        self.assertIsNone(self.bash('git push origin HEAD:refs/heads/feature/push'))
        self.git('config', '--unset-all', 'remote.origin.push')
        self.git('checkout', 'main')
        self.assert_guarded(self.bash('git push origin @'), 'claude', 'push')

    def test_push_urls_rewrites_and_unreadable_metadata_cannot_grant_permission(self):
        self.policy()
        for key, value in [('remote.origin.pushurl', 'https://example.invalid/other.git'),
                           ('url.https://example.invalid/other.pushInsteadOf', 'https://example.invalid/')]:
            self.git('config', key, value)
            self.assert_guarded(self.bash('git push origin feature/push'), 'claude', 'push')
            self.git('config', '--unset-all', key)
        with (self.root / '.git/config').open('ab') as stream:
            stream.write(b'\n[branch "unused"]\n merge = bad\xff\n')
        self.assert_guarded(self.bash('git push origin main'), 'claude', 'push')

    def test_codex_and_self_protection_are_not_relaxed(self):
        folder = self.policy()
        for cmd in ('git push origin feature/push', 'git push',
                    'git subtree push --prefix=sdk origin feature/push'):
            self.assert_denied(self.bash(cmd, 'codex'), 'push')
        self.assert_guarded(self.edit(str(folder / 'project.json'), 'allow', 'ask'), 'claude')
        self.assert_guarded(self.edit(str(folder / 'project.json'), 'allow', 'ask', 'codex'), 'codex')

    def test_bad_values_note_and_keep_core_ask(self):
        for value in ('ALLOW', 'deny', '', True, None, ['allow'], {'mode': 'allow'}):
            with self.subTest(value=value):
                self.policy(value)
                event = self.event('Bash', command='git push origin feature/push')
                done = self.run_guard(json.dumps(event))
                self.assertIn('guard.push', done.stderr)
                self.assertEqual(json.loads(done.stdout)['hookSpecificOutput']['permissionDecision'], 'ask')


if __name__ == '__main__':
    unittest.main()
