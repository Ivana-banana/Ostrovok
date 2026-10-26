"""Tests for scripts/team.py (needs git on PATH). Run: python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
TEAM = os.path.join(SCRIPTS, "team.py")
sys.path.insert(0, SCRIPTS)

HAS_GIT = shutil.which("git") is not None


def sh(cwd, *args, check=True):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise AssertionError(f"{args}: {r.stderr}")
    return r.stdout.strip()


def team(cwd, *args, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([sys.executable, TEAM, *args], cwd=cwd, capture_output=True,
                       encoding="utf-8", errors="replace", env=e)
    if r.returncode != 0:
        raise AssertionError(f"team.py {args} failed: {r.stderr}{r.stdout}")
    return json.loads(r.stdout)


def clone(root, remote, name):
    path = os.path.join(root, name)
    sh(root, "git", "clone", "-q", remote, path)
    sh(path, "git", "config", "user.email", f"{name}@example.com")
    sh(path, "git", "config", "user.name", name)
    return path


PLAN = [{"id": "A", "title": "a"}, {"id": "B", "title": "b"}, {"id": "M", "title": "merge", "merge": True}]


@unittest.skipUnless(HAS_GIT, "git not installed")
class TeamGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.remote = os.path.join(self.root, "remote.git")
        sh(self.root, "git", "init", "-q", "--bare", "-b", "master", self.remote)
        self.lead = clone(self.root, self.remote, "anna")
        with open(os.path.join(self.lead, "README.md"), "w") as f:
            f.write("hi\n")
        sh(self.lead, "git", "add", "README.md")
        sh(self.lead, "git", "commit", "-qm", "init")
        sh(self.lead, "git", "push", "-q", "origin", "master")
        self.plan_file = os.path.join(self.root, "plan.json")
        with open(self.plan_file, "w") as f:
            json.dump(PLAN, f)

    def tearDown(self):
        self.tmp.cleanup()

    def lead_flow(self, cwd, member="anna"):
        team(cwd, "init", "--run", "r1", "--member", member, "--input", "hack", "--team", "2")
        for s in ("read", "probe", "grill", "brief"):
            team(cwd, "stage", "--run", "r1", "--member", member, s, "done")
        team(cwd, "plan", "--run", "r1", "--member", member, "scenarios", "--tasks-file", self.plan_file)
        team(cwd, "start", "--run", "r1", "--member", member, "scenarios", "A")
        with open(os.path.join(cwd, ".arch", "r1", "tasks", "scenarios", f"A.{member}.md"), "w") as f:
            f.write("output\n")
        team(cwd, "done", "--run", "r1", "--member", member, "scenarios", "A")
        team(cwd, "sync", "--run", "r1", "--member", member)

    def test_user_branch_never_touched(self):
        # an unpushed local commit, a dirty tracked file and an untracked file
        with open(os.path.join(self.lead, "local.txt"), "w") as f:
            f.write("mine\n")
        sh(self.lead, "git", "add", "local.txt")
        sh(self.lead, "git", "commit", "-qm", "LOCAL UNPUSHED")
        with open(os.path.join(self.lead, "README.md"), "a") as f:
            f.write("edit\n")
        with open(os.path.join(self.lead, "scratch.txt"), "w") as f:
            f.write("x\n")
        head_before = sh(self.lead, "git", "rev-parse", "HEAD")
        status_before = sh(self.lead, "git", "status", "--porcelain")

        self.lead_flow(self.lead)

        sh(self.lead, "git", "fetch", "-q", "origin")
        self.assertNotIn("LOCAL UNPUSHED", sh(self.lead, "git", "log", "--format=%s", "origin/master"))
        self.assertEqual(sh(self.lead, "git", "rev-parse", "HEAD"), head_before)
        self.assertNotIn("arch(", sh(self.lead, "git", "log", "--format=%s", "master"))
        self.assertEqual(sh(self.lead, "git", "status", "--porcelain"), status_before)
        self.assertIn("arch(r1)", sh(self.lead, "git", "log", "--format=%s", "origin/arch/r1"))
        # the arch branch is orphan: no project history in it
        self.assertNotIn("init\n", sh(self.lead, "git", "log", "--format=%s", "origin/arch/r1") + "\n")
        self.assertIn("/.arch/", read(os.path.join(self.lead, ".git", "info", "exclude")))

    def test_two_clones_see_each_other(self):
        self.lead_flow(self.lead)
        boris = clone(self.root, self.remote, "boris")
        status_before = sh(boris, "git", "status", "--porcelain")
        team(boris, "join", "--run", "r1", "--member", "boris")
        nxt = team(boris, "next", "--run", "r1", "--member", "boris", "--wait", "0")
        self.assertEqual(nxt["action"], "task")
        self.assertEqual(nxt["task"]["id"], "B")
        team(boris, "start", "--run", "r1", "--member", "boris", "scenarios", "B")
        with open(os.path.join(boris, ".arch", "r1", "tasks", "scenarios", "B.boris.md"), "w") as f:
            f.write("b\n")
        team(boris, "done", "--run", "r1", "--member", "boris", "scenarios", "B")
        st = team(self.lead, "status", "--run", "r1")
        team(self.lead, "sync", "--run", "r1", "--member", "anna")
        st = team(self.lead, "status", "--run", "r1")
        tasks = {t["id"]: t for s in st["stages"] for t in (s.get("tasks") or [])}
        self.assertEqual(tasks["B"]["state"], "done")
        self.assertEqual(tasks["B"]["by"], "boris")
        self.assertIn("boris", st["members"])
        self.assertEqual(sh(boris, "git", "status", "--porcelain"), status_before)
        self.assertEqual(sh(boris, "git", "log", "--format=%s", "master"), "init")

    def test_remote_name_not_hardcoded(self):
        sh(self.lead, "git", "remote", "rename", "origin", "upstream")
        self.lead_flow(self.lead)
        refs = sh(self.root, "git", "--git-dir", self.remote, "branch", "--list", "arch/*")
        self.assertIn("arch/r1", refs)

    def test_worktrees_without_remote_share_state(self):
        sh(self.lead, "git", "remote", "remove", "origin")
        second = os.path.join(self.root, "anna-2")
        sh(self.lead, "git", "worktree", "add", "-q", "--detach", second)
        self.lead_flow(self.lead)
        team(second, "join", "--run", "r1", "--member", "boris")
        st = team(self.lead, "status", "--run", "r1")
        team(self.lead, "sync", "--run", "r1", "--member", "anna")
        st = team(self.lead, "status", "--run", "r1")
        self.assertIn("boris", st["members"])

    def test_no_git_shared_folder(self):
        env = {"ARCH_NO_GIT": "1"}
        r = team(self.lead, "init", "--run", "r2", "--member", "anna", "--input", "x", "--team", "2", env=env)
        self.assertEqual(r["sync"]["sync"], "skipped")
        team(self.lead, "join", "--run", "r2", "--member", "boris", env=env)
        st = team(self.lead, "status", "--run", "r2", env=env)
        self.assertEqual(sorted(st["members"]), ["anna", "boris"])
        self.assertEqual(sh(self.lead, "git", "status", "--porcelain"), "")


@unittest.skipUnless(HAS_GIT, "git not installed")
class NextWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        env = {"ARCH_NO_GIT": "1"}
        team(self.d, "init", "--run", "w", "--member", "anna", "--input", "x", "--team", "2", env=env)
        team(self.d, "join", "--run", "w", "--member", "boris", env=env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wait_returns_in_time_with_retry_after(self):
        t0 = time.monotonic()
        r = team(self.d, "next", "--run", "w", "--member", "boris", "--wait", "2", env={"ARCH_NO_GIT": "1"})
        self.assertLess(time.monotonic() - t0, 2 + 5)
        self.assertEqual(r["action"], "wait")
        self.assertIn("retry_after", r)

    def test_wait_is_capped(self):
        import team as T
        old_cwd, old_max = os.getcwd(), T.MAX_WAIT
        os.environ["ARCH_NO_GIT"] = "1"
        try:
            os.chdir(self.d)
            T.MAX_WAIT = 1
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                T.cmd_next(types.SimpleNamespace(run="w", member="boris", wait=9999))
            r = json.loads(buf.getvalue())
            self.assertEqual(r["wait_capped"], {"requested": 9999, "used": 1, "why": r["wait_capped"]["why"]})
        finally:
            T.MAX_WAIT = old_max
            os.chdir(old_cwd)
            del os.environ["ARCH_NO_GIT"]

    def test_default_cap_is_540(self):
        import team as T
        self.assertEqual(T.MAX_WAIT, 540)


if __name__ == "__main__":
    unittest.main()
