# Grill — the user decides the conditions, with your deep recommendation (stage 2)

Contents: 1. What the user decides · 2. Tool · 3. A question with a deep recommendation · 4. Rounds · 5. Weights · 6. Autopilot · 7. Output

## 1. What the user decides

The user owns the **conditions**; you own the facts. Before asking, read `00-inventory.md`, `00b-probe.md` and `00c-domain.md`: never ask what the folder, the probe or the primer already answers. Ask only:
- goal and what counts as success; what the judges will run (if the input does not say);
- users and how they work today;
- hard constraints: deadline, team size and skills, budget, stack or models fixed, hardware, privacy, latency;
- actions in the outside world (writes, sends, pays, deletes) and who confirms them;
- no-gos, and what the team wants to avoid;
- contradictions and gaps found in stages 0–1 (they come first);
- the swing weights (section 5).

Never ask "which architecture" or "multi-agent or not": that is what the run is for.

## 2. Tool

If the `grilling` skill is available (Claude Code: `/grilling`; read its SKILL.md), run the grill through it: decision tree, rounds of the current front, the question window inside tmux or the text format outside it. Topic: `"<task> — conditions for the architecture"`. Use `--file 01a-grill.md` so the answers land in the working directory.

Without it: the same question format as text in chat, one round per message.

## 3. A question with a deep recommendation

Every question has 2–4 options with a rating (green / yellow / orange / red) and exactly one ★ option. The user values your recommendation more than the question, so each ★ comes with a **deep recommendation** in the question body (4–8 lines):

- **Recommendation:** the ★ option in one line.
- **Why:** the facts behind it — from the probe, the primer, prior art seen so far, the judging criteria; cite numbers.
- **What it changes in the design:** which candidates, scenarios or criteria this answer moves ("if the checker gives GNSS only in the first 1.5 s, every option must be evaluated dead-reckoning-only; options that rely on mid-route GNSS lose quality").
- **Risk if wrong:** what happens if the user picks the ★ and reality differs, and how the run will notice.
- **When another option is better:** one line per serious alternative.

Good deep recommendations are specific to this task. "It depends" is not a recommendation; pick, and say what would change your mind.

## 4. Rounds

Front = decisions whose prerequisites are decided. Ask the whole front in one round (3–7 questions), wait, then the next front. Stop when the front is empty. `progress.py wait "Answer grill round N (k questions)"` while waiting. After the last round, show the decided conditions in 5–10 lines and ask for confirmation once.

## 5. Weights (swing)

The last round sets the weights (`rubric.md`): "Improve one criterion from worst to best — which first? second? … The first is worth 100; how much is the second?" Offer a ★ ordering with a deep recommendation built from the judging criteria (hackathon: jury criteria 1:1 plus build_speed and reliability, `rubric.md`). Convert with `score.py weights --swing …`.

## 6. Autopilot

Write every round in exactly the same format to `01a-grill.md` (questions, options, ratings, ★, deep recommendation), take each ★ as the answer with status `assumed`, and list them in the brief under "Assumptions taken on autopilot — please review", each with the criteria / scenarios it affects. Do not open the question window.

## 7. Output

`01a-grill.md`: every question, the options, the ★ and its deep recommendation, the answer (user / assumed), and the decided conditions. Then `progress.py impact grill --note "<conditions that most constrain the design>"`.
