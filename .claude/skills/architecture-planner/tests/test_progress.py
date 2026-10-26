"""Tests for scripts/progress.py. Run: python -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


def run(workdir, *args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "progress.py"), "--workdir", workdir, *args],
                          capture_output=True, encoding="utf-8", errors="replace", env=e)


class Progress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.assertEqual(run(self.d, "init", "--task", "Тест").returncode, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def state(self):
        with open(os.path.join(self.d, "progress.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_views_rendered(self):
        for f in ("progress.html", "PROGRESS.md", "progress.json"):
            self.assertTrue(os.path.exists(os.path.join(self.d, f)))

    def test_score_must_be_number_0_100(self):
        run(self.d, "cand", "add", "C1", "Loop", "idea")
        self.assertNotEqual(run(self.d, "cand", "set", "C1", "--score", "отлично").returncode, 0)
        self.assertNotEqual(run(self.d, "cand", "set", "C1", "--score", "101").returncode, 0)
        self.assertEqual(run(self.d, "cand", "set", "C1", "--score", "72.46").returncode, 0)
        self.assertEqual(self.state()["candidates"]["C1"]["score"], 72.5)

    def test_wave_flag(self):
        run(self.d, "cand", "add", "C1", "Loop", "idea", "--wave", "A")
        run(self.d, "cand", "add", "C2", "Glue", "idea", "--wave", "B")
        st = self.state()
        self.assertEqual(st["candidates"]["C1"]["wave"], "A")
        self.assertEqual(st["candidates"]["C2"]["wave"], "B")
        self.assertNotEqual(run(self.d, "cand", "add", "C3", "x", "--wave", "Z").returncode, 0)

    def test_questions_sorted_by_voi(self):
        run(self.d, "q", "add", "R1", "low", "--voi", "0.01")
        run(self.d, "q", "add", "R2", "high", "--voi", "0.30")
        run(self.d, "q", "add", "R3", "none")
        run(self.d, "stage", "research", "start")
        todos = json.loads(run(self.d, "todos").stdout)["todos"]
        subs = [t["content"].strip().split(":")[0] for t in todos if t["content"].startswith("   R")]
        self.assertEqual(subs, ["R2", "R1", "R3"])
        html = read(os.path.join(self.d, "progress.html"))
        self.assertLess(html.index(">R2<"), html.index(">R1<"))
        self.assertNotEqual(run(self.d, "q", "set", "R1", "--voi", "2").returncode, 0)

    def test_old_cli_still_works(self):
        for args in (("stage", "read", "start"), ("now", "x"), ("next", "a", "b"),
                     ("cand", "add", "C1", "n", "i"), ("cand", "set", "C1", "finalist", "--score", "50"),
                     ("q", "add", "R1", "t", "--cand", "C1"), ("q", "set", "R1", "answered", "--answer", "y"),
                     ("proto", "add", "C1"), ("proto", "set", "C1", "done", "--result", "r"),
                     ("wait", "w"), ("log", "l"), ("render",), ("status",), ("todos",)):
            r = run(self.d, *args)
            self.assertEqual(r.returncode, 0, f"{args}: {r.stderr}")

    def test_autopilot(self):
        run(self.d, "autopilot", "yes")
        self.assertTrue(self.state()["autopilot"])
        r = run(self.d, "wait", "Confirm the brief")
        self.assertIn('"autopilot": true', r.stdout)
        self.assertEqual(self.state()["waiting"], "")  # nothing blocks
        run(self.d, "log", "AUTOPILOT: accepted brief — recommended answers")
        md = read(os.path.join(self.d, "AUTOPILOT-DECISIONS.md"))
        self.assertIn("accepted brief", md)
        self.assertIn("AUTOPILOT", read(os.path.join(self.d, "progress.html")))

    def test_init_autopilot_with_budget(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, "init", "--task", "t", "--autopilot", "yes", "--budget", "5")
            st = json.loads(read(os.path.join(d, "progress.json")))
            self.assertTrue(st["autopilot"])
            self.assertEqual(st["budget"], 5)

    def test_legacy_console_encoding(self):
        run(self.d, "cand", "add", "C1", "Цикл", "идея")
        r = run(self.d, "todos", env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_single_in_progress_item(self):
        run(self.d, "stage", "research", "start")
        run(self.d, "q", "add", "R1", "t")
        run(self.d, "q", "set", "R1", "searching")
        todos = json.loads(run(self.d, "todos").stdout)["todos"]
        self.assertEqual(sum(t["status"] == "in_progress" for t in todos), 1)


if __name__ == "__main__":
    unittest.main()


class Impact(unittest.TestCase):
    def test_impact_log_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, "init", "--task", "t")
            self.assertEqual(run(d, "impact", "research", "--leader-before", "C1", "--leader-after", "C4",
                                 "--added", "gate:two-keys", "--final", "yes", "--minutes", "12",
                                 "--note", "backtest").returncode, 0)
            with open(os.path.join(d, "progress.json"), encoding="utf-8") as f:
                st = json.load(f)
            self.assertTrue(st["impact"][0]["leader_changed"])
            md = read(os.path.join(d, "STAGE-IMPACT.md"))
            self.assertIn("**C1 → C4**", md)
            self.assertIn("gate:two-keys", md)

    def test_new_stages_exist(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, "init", "--task", "t")
            for sid in ("probe", "craft"):
                self.assertEqual(run(d, "stage", sid, "start").returncode, 0)
