"""install.py — 가짜 ~/.claude를 대상으로만 돈다. 실제 홈은 건드리지 않는다."""
import contextlib
import io
import json
import unittest
from pathlib import Path

import install
from tests import tmpdir

AFPLAY = {"type": "command", "command": "afplay /System/Library/Sounds/Glass.aiff",
          "async": True}


def settings_with_afplay():
    return {
        "model": "opus[1m]",
        "hooks": {"Stop": [{"hooks": [dict(AFPLAY)]}],
                  "Notification": [{"hooks": [{"type": "command",
                                               "command": "afplay Tink.aiff"}]}]},
        "theme": "dark",
    }


STATUSLINE_BEFORE = (
    "#!/usr/bin/env python3\n"
    'DIM   = "\\033[2m"\n'
    'RESET = "\\033[0m"\n'
    + install.STATUSLINE_ANCHOR
    + "\nparts = []\n"
    + install.STATUSLINE_ORIGINAL
    + "\nprint(SEP.join(parts))\n"
)


class TestHookCommand(unittest.TestCase):
    def test_uses_the_given_checkout_not_a_hardcoded_path(self):
        cmd = install.hook_command("/somewhere/else/AgentPulse")
        self.assertEqual(
            cmd, "python3 /somewhere/else/AgentPulse/hooks/context_alert.py")

    def test_default_points_at_this_checkout(self):
        self.assertTrue(
            install.hook_command().endswith("/hooks/context_alert.py"))
        self.assertIn(str(install.REPO_ROOT), install.hook_command())


class TestAddHook(unittest.TestCase):
    def test_appends_without_disturbing_afplay(self):
        s, changed = install.add_hook(settings_with_afplay(), "python3 x.py")
        self.assertTrue(changed)
        self.assertEqual(install.stop_commands(s),
                         [AFPLAY["command"], "python3 x.py"])

    def test_is_idempotent(self):
        s, _ = install.add_hook(settings_with_afplay(), "python3 x.py")
        s2, changed = install.add_hook(s, "python3 x.py")
        self.assertFalse(changed)
        self.assertEqual(install.stop_commands(s2),
                         [AFPLAY["command"], "python3 x.py"])

    def test_does_not_mutate_the_input(self):
        original = settings_with_afplay()
        install.add_hook(original, "python3 x.py")
        self.assertEqual(install.stop_commands(original), [AFPLAY["command"]])

    def test_creates_the_structure_when_there_are_no_hooks(self):
        s, changed = install.add_hook({}, "python3 x.py")
        self.assertTrue(changed)
        self.assertEqual(install.stop_commands(s), ["python3 x.py"])

    def test_leaves_other_hook_events_alone(self):
        s, _ = install.add_hook(settings_with_afplay(), "python3 x.py")
        self.assertIn("Notification", s["hooks"])


class TestRemoveHook(unittest.TestCase):
    def test_removes_only_our_entry(self):
        s, _ = install.add_hook(settings_with_afplay(), "python3 x.py")
        s2, changed = install.remove_hook(s, "python3 x.py")
        self.assertTrue(changed)
        self.assertEqual(install.stop_commands(s2), [AFPLAY["command"]])

    def test_is_idempotent(self):
        s, changed = install.remove_hook(settings_with_afplay(), "python3 x.py")
        self.assertFalse(changed)
        self.assertEqual(install.stop_commands(s), [AFPLAY["command"]])


class TestStatuslinePatch(unittest.TestCase):
    def test_applies_the_warning_marker(self):
        text, changed = install.patch_statusline(STATUSLINE_BEFORE)
        self.assertTrue(changed)
        self.assertIn("⚠", text)
        self.assertIn(install.STATUSLINE_CONST, text)

    def test_is_idempotent(self):
        once, _ = install.patch_statusline(STATUSLINE_BEFORE)
        twice, changed = install.patch_statusline(once)
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_unpatch_restores_the_original_byte_for_byte(self):
        patched, _ = install.patch_statusline(STATUSLINE_BEFORE)
        back, changed = install.unpatch_statusline(patched)
        self.assertTrue(changed)
        self.assertEqual(back, STATUSLINE_BEFORE)

    def test_unpatch_is_idempotent(self):
        back, changed = install.unpatch_statusline(STATUSLINE_BEFORE)
        self.assertFalse(changed)
        self.assertEqual(back, STATUSLINE_BEFORE)

    def test_unknown_statusline_shape_stops_instead_of_guessing(self):
        with self.assertRaises(install.InstallError):
            install.patch_statusline("print('내가 만든 statusline')\n")


class TestValidate(unittest.TestCase):
    def test_missing_entry_is_reported(self):
        d = tmpdir()
        p = d / "settings.json"
        p.write_text(json.dumps(settings_with_afplay()), encoding="utf-8")
        with self.assertRaises(install.InstallError):
            install.validate_settings(p, [AFPLAY["command"], "python3 x.py"])

    def test_broken_json_is_reported(self):
        d = tmpdir()
        p = d / "settings.json"
        p.write_text("{truncated", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            install.validate_settings(p, [])

    def test_read_settings_refuses_broken_json(self):
        d = tmpdir()
        p = d / "settings.json"
        p.write_text("{truncated", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            install.read_settings(p)


class FakeClaudeDir(unittest.TestCase):
    """항상 임시 디렉터리를 대상으로 돈다 — 실제 ~/.claude는 쓰지 않는다."""

    def setUp(self):
        self.claude = tmpdir() / ".claude"
        self.claude.mkdir()
        self.settings = self.claude / "settings.json"
        self.statusline = self.claude / "statusline.py"
        self.settings.write_text(
            json.dumps(settings_with_afplay(), indent=2), encoding="utf-8")
        self.statusline.write_text(STATUSLINE_BEFORE, encoding="utf-8")
        self.repo = "/repo/AgentPulse"
        self.cmd = install.hook_command(self.repo)

    def install(self, **kw):
        return install.run(self.claude, repo_root=self.repo,
                           out=lambda *a: None, **kw)

    def commands(self):
        return install.stop_commands(json.loads(
            self.settings.read_text(encoding="utf-8")))


class TestRunInstall(FakeClaudeDir):
    def test_registers_hook_and_patches_statusline(self):
        changed = self.install()
        self.assertTrue(changed["hook"])
        self.assertTrue(changed["statusline"])
        self.assertEqual(self.commands(), [AFPLAY["command"], self.cmd])
        self.assertIn("⚠", self.statusline.read_text(encoding="utf-8"))

    def test_backs_up_both_files_before_touching_them(self):
        changed = self.install(stamp="20260922-131400")
        for name in ("settings.json", "statusline.py"):
            bak = self.claude / f"{name}.bak-pulse-20260922-131400"
            self.assertTrue(bak.exists(), name)
        # 백업은 고치기 전 내용이어야 한다
        self.assertEqual(
            (self.claude / "statusline.py.bak-pulse-20260922-131400")
            .read_text(encoding="utf-8"), STATUSLINE_BEFORE)
        self.assertIsNotNone(changed["backups"]["settings"])

    def test_running_twice_changes_nothing_the_second_time(self):
        self.install()
        after_first = self.settings.read_text(encoding="utf-8")
        changed = self.install()
        self.assertFalse(changed["hook"])
        self.assertFalse(changed["statusline"])
        self.assertEqual(self.commands(), [AFPLAY["command"], self.cmd])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), after_first)

    def test_settings_stays_valid_json_with_the_rest_intact(self):
        self.install()
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "opus[1m]")
        self.assertEqual(data["theme"], "dark")
        self.assertIn("Notification", data["hooks"])

    def test_missing_settings_file_is_created(self):
        self.settings.unlink()
        self.install()
        self.assertEqual(self.commands(), [self.cmd])

    def test_missing_statusline_is_skipped_not_created(self):
        self.statusline.unlink()
        changed = self.install()
        self.assertFalse(changed["statusline"])
        self.assertFalse(self.statusline.exists())
        self.assertEqual(self.commands(), [AFPLAY["command"], self.cmd])

    def test_broken_settings_stops_before_writing(self):
        self.settings.write_text("{truncated", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            self.install()
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{truncated")

    def test_validation_failure_restores_the_backup(self):
        real = install.validate_settings

        def boom(path, expected):
            raise install.InstallError("일부러 실패")

        install.validate_settings = boom
        self.addCleanup(setattr, install, "validate_settings", real)

        before = self.settings.read_text(encoding="utf-8")
        with self.assertRaises(install.InstallError):
            self.install()
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)


class TestRunUninstall(FakeClaudeDir):
    def test_removes_hook_and_reverts_statusline(self):
        self.install()
        changed = self.install(uninstall=True)
        self.assertTrue(changed["hook"])
        self.assertTrue(changed["statusline"])
        self.assertEqual(self.commands(), [AFPLAY["command"]])
        self.assertEqual(self.statusline.read_text(encoding="utf-8"),
                         STATUSLINE_BEFORE)

    def test_is_idempotent(self):
        self.install()
        self.install(uninstall=True)
        changed = self.install(uninstall=True)
        self.assertFalse(changed["hook"])
        self.assertFalse(changed["statusline"])
        self.assertEqual(self.commands(), [AFPLAY["command"]])

    def test_never_touches_the_afplay_entry(self):
        self.install()
        self.install(uninstall=True)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"],
                         AFPLAY["command"])


class TestCliDoesNotDefaultToTheRealHome(unittest.TestCase):
    def test_claude_dir_is_injectable(self):
        d = tmpdir() / ".claude"
        # main은 사람에게 보고하려고 찍는다 — 테스트 출력까지 더럽히지 않게 삼킨다
        with contextlib.redirect_stdout(io.StringIO()):
            rc = install.main(["--claude-dir", str(d), "--repo-root", "/repo/x"])
        self.assertEqual(rc, 0)
        self.assertTrue((d / "settings.json").exists())
        self.assertEqual(
            install.stop_commands(json.loads(
                (d / "settings.json").read_text(encoding="utf-8"))),
            ["python3 /repo/x/hooks/context_alert.py"])


if __name__ == "__main__":
    unittest.main()
