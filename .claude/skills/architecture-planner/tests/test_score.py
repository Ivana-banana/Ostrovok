"""Tests for scripts/score.py. Run: python -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import score  # noqa: E402

ANCH = {str(i): f"level {i}" for i in range(1, 6)}


def crit(*pairs):
    return [{"id": c, "name": c, "weight": w, "anchors": dict(ANCH)} for c, w in pairs]


def entry(cand, criterion, s, lens="panel", ev="researched", just="anchor"):
    return {"cand": cand, "criterion": criterion, "lens": lens, "score": s,
            "evidence": ev, "justification": just, "source": "test"}


def doc(criteria, entries, finalists=None):
    d = {"criteria": criteria, "entries": entries}
    if finalists:
        d["finalists"] = finalists
    return d


def run(workdir, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "score.py"), "--workdir", workdir, *args],
                          capture_output=True, encoding="utf-8")


class Formula(unittest.TestCase):
    W = {"a": 60, "b": 40}

    def test_all_ones_is_zero(self):
        self.assertEqual(score.total_of({"a": 1, "b": 1}, self.W), 0)

    def test_all_fives_is_hundred(self):
        self.assertEqual(score.total_of({"a": 5, "b": 5}, self.W), 100)

    def test_all_threes_is_fifty(self):
        self.assertEqual(score.total_of({"a": 3, "b": 3}, self.W), 50)

    def test_weighted(self):
        # (5-1)/4*60 + (1-1)/4*40 = 60
        self.assertAlmostEqual(score.total_of({"a": 5, "b": 1}, self.W), 60)

    def test_zero_weight_ignored(self):
        self.assertEqual(score.total_of({"a": 5, "b": 1, "c": 1}, {"a": 100, "b": 0, "c": 0}), 100)


class Aggregation(unittest.TestCase):
    def setUp(self):
        self.s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 4, "rel"), entry("C1", "q", 2, "bld"), entry("C1", "q", 5, "stk"),
            entry("C1", "l", 3, "rel"), entry("C1", "l", 3, "bld"), entry("C1", "l", 4, "stk"),
        ])

    def test_median_over_lenses(self):
        cells = score.cell_table(self.s)
        self.assertEqual(cells["C1"]["q"]["score"], 4)
        self.assertEqual(cells["C1"]["l"]["score"], 3)

    def test_total_uses_medians(self):
        agg = score.aggregate(self.s, {})
        # (4-1)/4*50 + (3-1)/4*50 = 37.5 + 25 = 62.5
        self.assertAlmostEqual(agg["candidates"]["C1"]["total"], 62.5)

    def test_disagreement_flagged_at_spread_2(self):
        agg = score.aggregate(self.s, {})
        flagged = {(d["cand"], d["criterion"]) for d in agg["disagreements"]}
        self.assertIn(("C1", "q"), flagged)      # 2..5, spread 3
        self.assertNotIn(("C1", "l"), flagged)   # 3..4, spread 1

    def test_measured_overrides_estimates(self):
        before = score.aggregate(self.s, {})["candidates"]["C1"]["total"]
        self.s["entries"].append(entry("C1", "q", 1, "prototype", ev="measured"))
        cells = score.cell_table(self.s)
        self.assertEqual(cells["C1"]["q"]["score"], 1)
        self.assertEqual(cells["C1"]["q"]["evidence"], "measured")
        after = score.aggregate(self.s, {})["candidates"]["C1"]["total"]
        self.assertAlmostEqual(after, 25.0)
        self.assertLess(after, before)

    def test_screen_scores_replaced_by_panel(self):
        s = doc(crit(("q", 100)), [entry("C1", "q", 5, "screen", ev="assumption"),
                                   entry("C1", "q", 2, "rel", ev="assumption"),
                                   entry("C1", "q", 2, "bld", ev="assumption")])
        cell = score.cell_table(s)["C1"]["q"]
        self.assertEqual(cell["score"], 2)
        self.assertEqual(cell["spread"], 0)
        only_screen = doc(crit(("q", 100)), [entry("C2", "q", 4, "screen", ev="assumption")])
        self.assertEqual(score.cell_table(only_screen)["C2"]["q"]["score"], 4)

    def test_labels_resolved_through_map(self):
        s = doc(crit(("q", 100)), [entry("A", "q", 5)])
        agg = score.aggregate(s, {"labels": {"A": "C7"}})
        self.assertIn("C7", agg["candidates"])

    def test_confidence_from_evidence(self):
        s = doc(crit(("q", 70), ("l", 30)), [entry("C1", "q", 3, ev="measured"),
                                             entry("C1", "l", 3, ev="assumption")])
        agg = score.aggregate(s, {})
        self.assertEqual(agg["candidates"]["C1"]["confidence"], "high")


class Validate(unittest.TestCase):
    def base(self):
        return doc(crit(("q", 60), ("l", 40)), [entry("C1", "q", 3), entry("C1", "l", 3)])

    def errors(self, s):
        return score.validate(s)["errors"]

    def test_valid(self):
        self.assertEqual(self.errors(self.base()), [])

    def test_score_out_of_range(self):
        s = self.base(); s["entries"][0]["score"] = 6
        self.assertTrue(any("outside 1..5" in e for e in self.errors(s)))

    def test_score_not_number(self):
        s = self.base(); s["entries"][0]["score"] = "great"
        self.assertTrue(any("not a number" in e for e in self.errors(s)))

    def test_bad_evidence(self):
        s = self.base(); s["entries"][0]["evidence"] = "vibes"
        self.assertTrue(any("evidence" in e for e in self.errors(s)))

    def test_weights_must_sum_to_100(self):
        s = self.base(); s["criteria"][0]["weight"] = 50
        self.assertTrue(any("sum to 90" in e for e in self.errors(s)))

    def test_justification_required(self):
        s = self.base(); s["entries"][0]["justification"] = ""
        self.assertTrue(any("missing justification" in e for e in self.errors(s)))

    def test_hole_detected(self):
        s = self.base(); s["finalists"] = ["C1", "C2"]
        s["entries"].append(entry("C2", "q", 4))
        self.assertTrue(any("hole: finalist C2" in e and "for l" in e for e in self.errors(s)))

    def test_anchors_required(self):
        s = self.base(); del s["criteria"][0]["anchors"]["3"]
        self.assertTrue(any("anchors missing" in e for e in self.errors(s)))

    def test_unknown_criterion(self):
        s = self.base(); s["entries"].append(entry("C1", "zzz", 3))
        self.assertTrue(any("unknown criterion" in e for e in self.errors(s)))

    def test_zero_weight_needs_no_scores(self):
        s = self.base(); s["criteria"].append({"id": "x", "weight": 0})
        self.assertEqual(self.errors(s), [])


class Sensitivity(unittest.TestCase):
    def test_dominant_leader_wins_always(self):
        s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 5, ev="measured"), entry("C1", "l", 5, ev="measured"),
            entry("C2", "q", 2, ev="measured"), entry("C2", "l", 2, ev="measured")])
        r = score.sensitivity(s, {}, 500, 1)
        self.assertEqual(r["leader"], "C1")
        self.assertEqual(r["win_share"]["C1"], 1.0)
        self.assertEqual(r["verdict"], "robust")

    def test_near_tie_is_about_even(self):
        # mirror images: C1 strong on q, C2 strong on l, equal weights
        s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 5), entry("C1", "l", 1), entry("C2", "q", 1), entry("C2", "l", 5)])
        r = score.sensitivity(s, {}, 2000, 7)
        self.assertAlmostEqual(r["win_share"]["C1"], 0.5, delta=0.06)
        self.assertAlmostEqual(r["win_share"]["C2"], 0.5, delta=0.06)
        self.assertEqual(r["verdict"], "tie")

    def test_deterministic_with_seed(self):
        s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 4, ev="assumption"), entry("C1", "l", 3, ev="walkthrough"),
            entry("C2", "q", 3, ev="assumption"), entry("C2", "l", 4, ev="assumption")])
        a = score.sensitivity(s, {}, 300, 3)
        b = score.sensitivity(s, {}, 300, 3)
        self.assertEqual(a["win_share"], b["win_share"])

    def test_critical_weight_found(self):
        s = doc(crit(("q", 60), ("l", 40)), [
            entry("C1", "q", 5), entry("C1", "l", 2), entry("C2", "q", 3), entry("C2", "l", 5)])
        r = score.sensitivity(s, {}, 200, 1)
        crits = {(c["criterion"], c["direction"]) for c in r["critical_weights"]}
        self.assertTrue(("l", "up") in crits or ("q", "down") in crits)


class VOI(unittest.TestCase):
    def setUp(self):
        self.s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 5), entry("C1", "l", 4),     # leader, 87.5
            entry("C2", "q", 4), entry("C2", "l", 4),     # second, 75
            entry("C3", "q", 2), entry("C3", "l", 2)])    # far third, 25

    def test_irrelevant_question_has_zero_voi(self):
        plan = {"questions": [{"id": "R1", "p_yes": "medium",
                               "affects": [{"cand": "C3", "criterion": "q", "if_yes": 3, "if_no": 1}]}]}
        r = score.voi(self.s, {}, plan, 500, 5)
        self.assertEqual(r["questions"][0]["voi"], 0)
        self.assertEqual(r["questions"][0]["flips_leader_if"], [])

    def test_leader_dropping_question_ranks_first(self):
        plan = {"questions": [
            {"id": "R-irrelevant", "p_yes": "medium",
             "affects": [{"cand": "C3", "criterion": "l", "if_yes": 3, "if_no": 2}]},
            {"id": "R-key", "p_yes": "medium",
             "affects": [{"cand": "C1", "criterion": "q", "if_yes": 5, "if_no": 2}]}]}
        r = score.voi(self.s, {}, plan, 500, 5)
        self.assertEqual(r["questions"][0]["id"], "R-key")
        self.assertIn("no", r["questions"][0]["flips_leader_if"])
        self.assertGreater(r["questions"][0]["voi"], 0.05)

    def test_answered_questions_skipped_and_stop_rule(self):
        plan = {"questions": [{"id": "R1", "status": "answered", "p_yes": 0.5,
                               "affects": [{"cand": "C1", "criterion": "q", "if_yes": 5, "if_no": 1}]}]}
        r = score.voi(self.s, {}, plan, 200, 5)
        self.assertEqual(r["questions"], [])
        self.assertTrue(r["stop_research"])


class Diversity(unittest.TestCase):
    def test_flags_close_pairs(self):
        m = {"C1": {"flow": "loop", "act": "json", "mem": "none", "qc": "none"},
             "C2": {"flow": "loop", "act": "json", "mem": "none", "qc": "critic"},
             "C3": {"flow": "pipeline", "act": "code", "mem": "notes", "qc": "tests"}}
        r = score.diversity(m, 3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["too_similar"], [{"a": "C1", "b": "C2", "differ_on": 1}])

    def test_parse_markdown_matrix(self):
        md = ("# Candidates\n\n## Axis matrix\n\n| Candidate | Control flow | Action | Memory |\n"
              "|---|---|---|---|\n| C1 Single loop | loop | json | none |\n| C2 | pipeline | code | notes |\n")
        m = score.parse_matrix_md(md)
        self.assertEqual(set(m), {"C1", "C2"})
        self.assertEqual(m["C2"]["Memory"], "notes")


class CLI(unittest.TestCase):
    def test_end_to_end_cli(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run(d, "init", "--defaults", "--actions", "yes").returncode, 0)
            s = json.loads(read(os.path.join(d, "scores.json")))
            self.assertEqual(sum(c["weight"] for c in s["criteria"]), 100)
            self.assertIn("safety", [c["id"] for c in s["criteria"]])
            r = run(d, "add", "--cand", "C1", "--criterion", "quality", "--lens", "x", "--score", "9",
                    "--evidence", "researched", "--justification", "j")
            self.assertNotEqual(r.returncode, 0)
            r = run(d, "validate")
            self.assertEqual(r.returncode, 0)  # no finalists yet -> no holes
            r = run(d, "weights", "--swing", "quality=100", "latency=50", "safety=50")
            self.assertEqual(r.returncode, 0, r.stderr)
            w = json.loads(r.stdout)["weights"]
            self.assertEqual((w["quality"], w["latency"], w["safety"], w["cost"]), (50, 25, 25, 0))
            self.assertNotEqual(run(d, "weights", "--swing", "nope=10").returncode, 0)

    def test_sensitivity_subset(self):
        s = doc(crit(("q", 50), ("l", 50)), [
            entry("C1", "q", 5, ev="measured"), entry("C1", "l", 5, ev="measured"),
            entry("H1", "q", 3, ev="measured"), entry("H1", "l", 4, ev="measured"),
            entry("P1", "q", 3, ev="measured"), entry("P1", "l", 3, ev="measured")])
        r = score.sensitivity(s, {}, 200, 1, ["H1", "P1"])
        self.assertEqual(r["leader"], "H1")
        self.assertNotIn("C1", r["win_share"])


if __name__ == "__main__":
    unittest.main()


class V2Features(unittest.TestCase):
    """Rounds and scale-drift warning, replace history, anchors and cards commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, obj):
        with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        return os.path.join(self.d, name)

    def test_drift_warning_on_mixed_rounds(self):
        e1 = entry("C1", "q", 4); e1["round"] = 1
        e2 = entry("H1", "q", 4); e2["round"] = 2
        s = doc(crit(("q", 100)), [e1, e2])
        self.assertTrue(any("scale drift" in w for w in score.validate(s)["warnings"]))
        e1["round"] = 2
        self.assertFalse(any("scale drift" in w for w in score.validate(s)["warnings"]))

    def test_screen_and_prototype_do_not_count_as_rounds(self):
        e1 = entry("C1", "q", 4); e1["round"] = 2
        e2 = entry("H1", "q", 4); e2["round"] = 2
        e3 = entry("C1", "q", 5, lens="prototype", ev="measured"); e3["round"] = 3
        s = doc(crit(("q", 100)), [e1, e2, e3])
        self.assertIsNone(score.drift_warning(s))

    def test_replace_keeps_history_and_round_flag(self):
        self.write("scores.json", doc(crit(("q", 100)), [entry("C1", "q", 2)]))
        r = run(self.d, "import", self.write("e.json", [entry("C1", "q", 4)]), "--round", "2")
        self.assertEqual(json.loads(r.stdout)["replaced"], 1)
        st = json.loads(read(os.path.join(self.d, "scores.json")))
        self.assertEqual(st["entries"][0]["score"], 4)
        self.assertEqual(st["entries"][0]["round"], 2)
        self.assertEqual(st["history"][0]["score"], 2)

    def test_anchors_command(self):
        c = crit(("q", 100)); c[0]["anchors"] = {}
        self.write("scores.json", doc(c, []))
        a = self.write("a.json", {"q": {"1": "bad", "2": "weak", "3": "ok", "4": "good", "5": "best"}})
        self.assertEqual(run(self.d, "anchors", "--file", a).returncode, 0)
        st = json.loads(read(os.path.join(self.d, "scores.json")))
        self.assertEqual(st["criteria"][0]["anchors"]["5"], "best")
        b = self.write("b.json", {"nope": {"1": "x"}})
        self.assertNotEqual(run(self.d, "anchors", "--file", b).returncode, 0)

    def test_cards_command(self):
        self.write("scores.json", doc(crit(("q", 100)), [], finalists=["C1", "C2"]))
        self.assertEqual(run(self.d, "anonymize", "--lenses", "reliability,builder", "--seed", "7").returncode, 0)
        cj = self.write("cards.json", {"C1": {"components": "loop", "axes": "a", "bet": "b", "risk": "r", "actions": "none"},
                                  "C2": {"components": "pipe", "axes": "a", "bet": "b", "risk": "r", "actions": "none"},
                                  "_titles": {"components": "Компоненты"}})
        r = json.loads(run(self.d, "cards", "--cards", cj).stdout)
        self.assertTrue(r["ok"])
        txt = read(os.path.join(self.d, "cards-builder.md"))
        self.assertIn("**Компоненты:** loop", txt)
        self.assertNotIn("C1", txt)
        self.assertNotIn("What must be true", txt)  # builder lens gets no "bet"
