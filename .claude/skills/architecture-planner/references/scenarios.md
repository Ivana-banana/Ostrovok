# Test scenarios

Scenarios turn "is this architecture good?" into "what does it do on case S7?". Every critic walks architectures through them, prototypes run on them, and the final report shows which architecture handles which scenario. After the planning is done, they are the starting eval set for the implementation.

## Mix

For 10–20 scenarios (express: 6–8), aim roughly for:
- **Typical (40%)** — the bread-and-butter requests the system will see most.
- **Hard (25%)** — multi-step, long inputs, ambiguous requests, many tools, large data.
- **Edge (20%)** — empty or malformed input, missing data, external API failure or timeout, rate limit, very long conversation.
- **Adversarial / safety (15%)** — prompt injection in inputs or tool outputs, requests outside scope, attempts to trigger dangerous actions. If the system acts on the outside world (writes, sends, pays, deletes…), write at least **two** adversarial scenarios and mark both **critical**.

Base scenarios on the folder's real material (sample data, example tickets, judging criteria) wherever possible; invent only where nothing real exists, and mark invented ones.

**Required kinds** (in addition to the mix):
- one scenario per trap in `00b-probe.md` (units, late or out-of-order messages, silent sensors, markers, aggregator results);
- one **judge-conditions** scenario: exactly what the judges will run (signals available, unseen data, the official checker);
- one **recovery** scenario: a source or sensor fails and comes back; pass = no error spike and no jump after return;
- if the system has an interactive surface (editor, map, 2D/3D view): two **jury-click** scenarios with the flows a juror tries first (create, move, undo, save, reload);
- if the system depends on external sources: one **source-yield** scenario (share of usable results on ≥ 10 real queries).

## Who writes them

A separate subagent — the **scenario writer** — writes **all** scenarios (visible and holdout) in one go, from the input folder, `00-inventory.md`, `00b-probe.md` and the brief. It has never seen a candidate and never will. It writes `02-scenarios.md` (visible) and the sealed `02-scenarios-holdout.md`, and returns only the visible titles plus the holdout count and categories. The main agent may fix a factual error in a visible scenario but never weakens a pass criterion. Without subagents, write them yourself before stage 5 and say so in the report.

## Format (per scenario)

```
### S3 — <short title>  [typical | hard | edge | adversarial]
Input: <concrete input, or a precise description if it is large>
Context: <state, available data, what has happened before>
Expected behaviour: <what a good system does>
Pass criterion: <checkable condition: exact field values, contains X, no tool call Y, under N s…>
Weight: <normal | critical>   (critical = failing it disqualifies an architecture for this task)
Holdout: <no | yes>
```

Pass criteria must be checkable, by a script or by an LLM judge with a clear rubric. "Answers well" is not a criterion; "returns category = billing and cites the invoice ID" is.

## Holdout scenarios

Hold back **20–30 %** of the scenarios (full: 3–5; express: 1–2), spread across categories, with at least one hard case. They protect against designs tuned to the scenarios they were judged on — above all the hybrid, which is built after reading the critique.

- Holdout scenarios live in a separate file, `02-scenarios-holdout.md`; `02-scenarios.md` holds only the visible ones and says how many are held out (not which).
- **Never** put the holdout file into the context of wave A, wave B (stages 5 and 7), the critic panel (stage 11) or the hybrid design (stage 12). It is opened only for the holdout check in stage 12, for prototypes in stage 13 and for the report.
- With a subagent tool, have a subagent write the holdout scenarios straight to the sealed file in stage 4 and return only their count and categories. Without subagents, the holdout cannot be unseen once written, so write it at the start of the holdout check in stage 12 — after the H1 and P1 cards are frozen — from the brief alone, with the category mix fixed in stage 4; the report marks it "weaker holdout (same context)".

## Use downstream

- Critics: walk each finalist through all critical visible scenarios plus 3–4 others, and record handles / partial / fails with a reason.
- Hybrid: H1, P1 and the leader are checked on the holdout scenarios (`hybrid.md`).
- Prototype: run the spikes on all scenarios that can be executed in the time box (critical ones first).
- Report: coverage matrix architecture × scenario, holdout columns shown separately.
