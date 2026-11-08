"""Tests for kozu. They use local git repos and fake claude/codex CLIs, so they need no network."""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader("kozu", str(HERE.parent / "kozu"))
spec = importlib.util.spec_from_loader("kozu", loader)
kozu = importlib.util.module_from_spec(spec)
loader.exec_module(kozu)

FAKE_CLI = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    d = os.environ["FAKE_DIR"]
    name = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if args[-1:] == ["--json"] and args[:-1] in (["plugin", "marketplace", "list"], ["plugin", "list"]):
        key = "markets" if "marketplace" in args else "installed"
        print(open(os.path.join(d, f"{name}-{key}.json")).read())
    else:
        with open(os.path.join(d, "calls.log"), "a") as f:
            f.write(name + " " + " ".join(args) + "\\n")
""")


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class Sandbox(unittest.TestCase):
    """A temp kozu root, a fake upstream repo, and fake host CLIs first on PATH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        self.root.mkdir()
        for name, value in {"ROOT": self.root, "VENDOR": self.root / "vendor",
                            "STATE_DIR": self.root / ".state", "STATE_FILE": self.root / ".state" / "state.json",
                            "PLUGINS_FILE": self.root / "plugins.json", "PROFILE_FILE": self.root / "profile.json"}.items():
            p = mock.patch.object(kozu, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for cli in ("claude", "codex"):
            f = self.bin / cli
            f.write_text(FAKE_CLI.replace("python3", sys.executable.replace("\\", "/"), 1))
            f.chmod(0o755)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(self.tmp),
               "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.upstream = self.tmp / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q")

    def release(self, tag, market_name="demo"):
        u = self.upstream
        (u / ".claude-plugin").mkdir(exist_ok=True)
        (u / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": market_name, "plugins": [{"name": "demo", "source": "./sub"}]}))
        (u / "sub" / ".codex-plugin").mkdir(parents=True, exist_ok=True)
        (u / "sub" / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "version": tag}))
        git(u, "add", "-A")
        git(u, "commit", "-q", "-m", tag)
        git(u, "tag", "-a", tag, "-m", tag)  # annotated, so ls-remote must peel it
        return subprocess.run(["git", "-C", str(u), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def plugin(self, **extra):
        p = {"name": "demo", "repo": "x/demo", "url": str(self.upstream), "track": "release",
             "pin": {"ref": "none", "commit": ""},
             "claude": {"marketplace": "demo", "plugin": "demo"},
             "codex": {"marketplace": "demo", "plugin": "demo", "path": "sub"}}
        p.update(extra)
        return p

    def fake_hosts(self, claude_markets=(), claude_installed=(), codex_markets=(), codex_installed=()):
        for name, data in {"claude-markets": list(claude_markets), "claude-installed": list(claude_installed),
                           "codex-markets": {"marketplaces": list(codex_markets)},
                           "codex-installed": {"installed": list(codex_installed)}}.items():
            (self.tmp / f"{name}.json").write_text(json.dumps(data))
        (self.tmp / "calls.log").write_text("")

    def calls(self):
        return (self.tmp / "calls.log").read_text().splitlines()


class PureFunctions(unittest.TestCase):
    def test_latest_release_takes_highest_stable_semver(self):
        tags = {"v1.2.0": "a", "v1.10.0": "b", "v2.0.0-rc.1": "c", "nightly": "d", "1.9.9": "e"}
        self.assertEqual(kozu.latest_release(tags), ("v1.10.0", "b"))
        self.assertIsNone(kozu.latest_release({"nightly": "x"}))

    def test_upsert_block_appends_then_replaces_in_place(self):
        once = kozu.upsert_block("# Mine\n", "rule one")
        self.assertEqual(once, f"# Mine\n\n{kozu.BLOCK_START}\nrule one\n{kozu.BLOCK_END}\n")
        twice = kozu.upsert_block(once + "after\n", "rule two")
        self.assertIn("rule two", twice)
        self.assertNotIn("rule one", twice)
        self.assertTrue(twice.startswith("# Mine\n") and twice.endswith("after\n"))
        self.assertEqual(kozu.upsert_block(twice, "rule two"), twice)
        self.assertEqual(kozu.upsert_block("", "x"), f"{kozu.BLOCK_START}\nx\n{kozu.BLOCK_END}\n")

    def test_expand_defaults_and_overrides(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            self.assertEqual(kozu.expand("${XDG_CONFIG_HOME}/a"), Path.home() / ".config" / "a")
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/x/codex"}):
            self.assertEqual(kozu.expand("${CODEX_HOME}/AGENTS.md"), Path("/x/codex/AGENTS.md"))


class Bump(Sandbox):
    def test_bump_moves_to_newest_release_and_refuses_a_broken_one(self):
        self.release("v1.0.0")
        good = self.release("v1.1.0")
        kozu.save_json(kozu.PLUGINS_FILE, {"plugins": [self.plugin()]})
        msg = self.tmp / "msg"
        self.assertEqual(kozu.main(["bump", "--message-file", str(msg)]), 0)
        pin = json.loads(kozu.PLUGINS_FILE.read_text())["plugins"][0]["pin"]
        self.assertEqual(pin, {"ref": "v1.1.0", "commit": good})
        self.assertTrue(msg.read_text().startswith("demo none -> v1.1.0"))

        self.release("v1.2.0", market_name="renamed")
        self.assertEqual(kozu.main(["bump"]), 1)
        self.assertEqual(json.loads(kozu.PLUGINS_FILE.read_text())["plugins"][0]["pin"]["ref"], "v1.1.0")

    def test_pinned_track_is_left_alone(self):
        self.release("v1.0.0")
        kozu.save_json(kozu.PLUGINS_FILE, {"plugins": [self.plugin(track="pinned")]})
        self.assertEqual(kozu.main(["bump"]), 0)
        self.assertEqual(json.loads(kozu.PLUGINS_FILE.read_text())["plugins"][0]["pin"]["ref"], "none")


class Hosts(Sandbox):
    def setUp(self):
        super().setUp()
        self.commit = self.release("v1.0.0")
        self.p = self.plugin(pin={"ref": "v1.0.0", "commit": self.commit})
        self.assertTrue(kozu.checkout(kozu.VENDOR / "demo", str(self.upstream), self.commit))
        self.assertFalse(kozu.checkout(kozu.VENDOR / "demo", str(self.upstream), self.commit))

    def test_claude_moves_a_github_install_to_the_checkout(self):
        self.fake_hosts(claude_markets=[{"name": "demo", "source": "github", "repo": "x/demo"}],
                        claude_installed=[{"id": "demo@demo", "enabled": True}])
        errors = []
        kozu.sync_claude([self.p], errors)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls(), [
            "claude plugin uninstall demo@demo --keep-data",
            "claude plugin marketplace remove demo",
            f"claude plugin marketplace add {kozu.VENDOR / 'demo'}",
            "claude plugin install demo@demo",
        ])

    def test_claude_leaves_a_current_install_alone_and_enables_a_disabled_one(self):
        market = {"name": "demo", "source": "directory", "path": str(kozu.VENDOR / "demo")}
        self.fake_hosts(claude_markets=[market], claude_installed=[{"id": "demo@demo", "enabled": True}])
        kozu.sync_claude([self.p], [])
        self.assertEqual(self.calls(), [])
        self.fake_hosts(claude_markets=[market], claude_installed=[{"id": "demo@demo", "enabled": False}])
        kozu.sync_claude([self.p], [])
        self.assertEqual(self.calls(), ["claude plugin enable demo@demo"])

    def test_codex_installs_from_a_generated_marketplace_once_per_commit(self):
        root = kozu.STATE_DIR / "codex-marketplaces" / "demo"
        state = {}
        self.fake_hosts()
        self.assertTrue(kozu.sync_codex([self.p], state, [], force=False))
        self.assertEqual(self.calls(), [f"codex plugin marketplace add {root}", "codex plugin add demo@demo"])
        self.assertEqual(os.path.realpath(root / "plugin"), os.path.realpath(kozu.VENDOR / "demo" / "sub"))
        market = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text())
        self.assertEqual(market["plugins"][0]["source"], {"source": "local", "path": "./plugin"})
        self.assertEqual(state, {"codex": {"demo": self.commit}})

        self.fake_hosts(codex_markets=[{"name": "demo", "root": str(root)}],
                        codex_installed=[{"pluginId": "demo@demo", "installed": True}])
        self.assertFalse(kozu.sync_codex([self.p], state, [], force=False))
        self.assertEqual(self.calls(), [])
        kozu.sync_codex([self.p], state, [], force=True)
        self.assertEqual(self.calls(), ["codex plugin add demo@demo"])

    def test_codex_installer_runs_in_the_checkout_once_per_commit(self):
        p = dict(self.p, codex={"installer": [sys.executable, "-c", "open('ran', 'a').write('x')"]})
        state = {}
        self.fake_hosts()
        kozu.sync_codex([p], state, [], force=False)
        kozu.sync_codex([p], state, [], force=False)
        self.assertEqual((kozu.VENDOR / "demo" / "ran").read_text(), "x")
        kozu.sync_codex([p], state, [], force=True)
        self.assertEqual((kozu.VENDOR / "demo" / "ran").read_text(), "xx")


class SyncCommand(Sandbox):
    def test_sync_checks_out_installs_and_writes_profile_then_status_reports_ok(self):
        commit = self.release("v1.0.0")
        kozu.save_json(kozu.PLUGINS_FILE, {"plugins": [self.plugin(pin={"ref": "v1.0.0", "commit": commit})]})
        cfg = self.tmp / "cfg.json"
        kozu.save_json(kozu.PROFILE_FILE, {"config_files": {str(cfg): {"defaultMode": "ultracave"}}})
        self.fake_hosts()
        self.assertEqual(kozu.main(["sync", "--no-pull"]), 0)
        self.assertEqual(kozu.head(kozu.VENDOR / "demo"), commit)
        self.assertIn("claude plugin install demo@demo", self.calls())
        self.assertIn("codex plugin add demo@demo", self.calls())
        self.assertEqual(json.loads(cfg.read_text()), {"defaultMode": "ultracave"})

        self.fake_hosts(claude_installed=[{"id": "demo@demo", "readFromFolder": str(kozu.VENDOR / "demo")}],
                        codex_installed=[{"pluginId": "demo@demo", "installed": True}])
        out = io.StringIO()
        with redirect_stdout(out):
            kozu.main(["status"])
        self.assertRegex(out.getvalue(), r"demo +v1\.0\.0 \(\w{7}\) +ok +ok +ok")


class Profile(Sandbox):
    def test_profile_merges_json_and_writes_the_block(self):
        cfg = self.tmp / "cfg" / "caveman" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"keep": 1, "defaultMode": "caveman"}))
        agents = self.tmp / "codex" / "AGENTS.md"
        (self.root / "block.md").write_text("be brief\n")
        profile = {"config_files": {str(cfg): {"defaultMode": "ultracave"}},
                   "tokeneconomizer": {"toolprune": "on"},  # its CLI is not checked out here: skipped
                   "instruction_blocks": {str(agents): "block.md"}}
        errors = []
        kozu.apply_profile(profile, errors)
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(cfg.read_text()), {"keep": 1, "defaultMode": "ultracave"})
        self.assertIn("be brief", agents.read_text())


class Schedule(Sandbox):
    def test_cron_entry_is_added_once_and_removed_without_touching_others(self):
        fake = self.bin / "crontab"
        fake.write_text('#!/bin/sh\nf="$FAKE_DIR/crontab"\n'
                        'if [ "$1" = -l ]; then [ -f "$f" ] && cat "$f" || exit 1; else cat > "$f"; fi\n')
        fake.chmod(0o755)
        tab = self.tmp / "crontab"
        with mock.patch.object(kozu.sys, "platform", "linux"):
            args = kozu.argparse.Namespace(hours=6)
            self.assertEqual(kozu.cmd_schedule(args), 0)  # no crontab yet
            tab.write_text("0 1 * * * backup\n" + tab.read_text())
            kozu.cmd_schedule(args)
            lines = tab.read_text().splitlines()
            self.assertEqual(lines[0], "0 1 * * * backup")
            self.assertEqual(sum(kozu.CRON_MARK in ln for ln in lines), 1)
            self.assertIn(" */6 * * * PATH=", lines[1])
            self.assertIn(f"{kozu.ROOT / 'kozu'} sync >> ", lines[1])
            kozu.cmd_unschedule(args)
            self.assertEqual(tab.read_text(), "0 1 * * * backup\n")


if __name__ == "__main__":
    unittest.main()
