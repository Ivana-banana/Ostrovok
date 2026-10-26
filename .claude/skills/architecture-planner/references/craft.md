# Craft and safety-gates pass (stage 8)

Contents: 1. Why · 2. Who runs it · 3. The five questions · 4. Source strategy · 5. Evaluation honesty · 6. Output · 7. What changes downstream

## 1. Why

Blind comparisons with real hackathon teams showed one stable pattern. The skill wins on system properties (isolation of sources, caches, degradation, probabilistic risk). Teams win on two things the skill never generated:
1. **Craft found on live data and live sites:** non-headless browser against anti-bot, several search engines with fallback, telemetry defect masks, unit fixes, per-signal freshness thresholds, literal verification of every scraped fact.
2. **Safety gates and evaluation procedure built into the architecture:** a locked test set with a logged access command, dev → validation → test, bootstrap significance against the baseline, "two keys" (an independent re-check before any recommendation), evidence gates (a model that lost to the baseline on backtest may not drive actions), margin ≥ worst backtest error, rejection codes.

Expert reviewers at defences look for exactly the failures these prevent: numbers obtained in favourable conditions, a complex component that loses to a simple baseline, a rule tuned and checked on the same sample, an official checker never run on the final build, regressions after fault recovery, corrections applied as a jump.

This pass adds those things to every finalist **as components**, not as risks in a footnote.

## 2. Who runs it

A separate subagent, the **practitioner**, when available (otherwise a separate step with a fresh read). It gets: the brief, `00b-probe.md`, visible scenarios, the candidate cards, the judging criteria from the input, and this file. Not the critique, not prior-art rankings. Persona: "an engineer who has built this kind of system on real data and has sat on hackathon juries".

## 3. The five questions — for every candidate that will go to screening

1. **First hour on real data.** What breaks in the first hour against these data / these sites (use the probe)? Which concrete trick handles it? Name the trick, not the category ("Xvfb + non-headless Chromium via nodriver", not "anti-bot handling").
2. **Gates before action.** Which bad output can reach the user or the outside world, and which gate stops it? Pick from: independent re-check ("two keys"), evidence gate, margin ≥ backtest error, literal fact verification, freshness per signal, rejection with a code, NO_SOLUTION with a reason, rate/budget guard.
3. **Does every complex component beat its simple alternative?** For each model / filter / learned part, name the simple alternative (mean of two sensors, persistence, frequency, a rule) and the measurement that will show it wins. If it is not measured, the component ships behind a switch with the simple one as the default.
4. **What will the judges actually run?** Under the judge's conditions (signals available, unseen data, the official checker), what is the honest number, and how does the architecture make that number the one reported?
5. **Recovery and transitions.** After a sensor/source outage, a mode change, a correction: does the state recover smoothly (no jumps, no error spike after return)? What test proves it?
6. **Interactive surface** (editor, map, 2D/3D view, dashboard the jury will click). Treat it as a component with its own design, not as "UI at the end": the rendering library (fits the object count and interactions?), the editing model (commands with undo/redo, snapping, collision and constraint checks, validation messages, save/load round-trip), the state model shared with the core (one source of truth, no second copy of the domain rules in the UI), and e2e tests of the 5–10 flows the jury will try. Expert feedback on a "digital twin" project: "weak 2D, many problems in the editor" — the core was fine, the surface lost the points.

## 4. Source strategy (whenever the system depends on external sources)

Weak "additional sources" was the most common failure in teams' systems. For each source:
- list **at least three access paths** in order of robustness: official API or open dataset/registry dump → structured data embedded in the page (JSON-LD, `__NEXT_DATA__`, internal JSON endpoints visible in the page) → plain HTTP with a browser TLS fingerprint → a real browser (non-headless if needed) → partner feeds or aggregators;
- state the **yield**: the share of returned items that are actually usable (a company with a tax id, a price for the exact product). Use the probe's numbers; if none, the prototype must measure it on ≥ 10 queries;
- state the **verification**: how a fact is confirmed before display (literal match on the cached page, checksum, cross-source match);
- state the **degradation**: what the user sees when the source is down or blocked.

A source with an unmeasured yield is a risk at evidence `assumption`, never a strength.

## 5. Evaluation honesty (for ML / data / estimation tasks)

Every finalist gets an **evaluation component** in its card:
- split by whole sessions / events / time, never by rows; duplicates removed by hash;
- dev for tuning, validation for model choice, test locked behind one logged command;
- the simple baseline evaluated with the same script;
- significance (paired bootstrap over sessions/events) for every claimed improvement;
- a rule or threshold tuned on data X is never reported on data X;
- the official checker (if any) runs on the final build in CI.

## 6. Output — `03c-craft.md`

```markdown
# Craft and safety-gates pass

## Per candidate
### C3
- First hour: <trap> → <trick> (probe fact #)
- Gates: <gate> — stops <failure>
- Complex vs simple: <component> vs <simple alternative> — measured by <how>; default = <which>
- Judge conditions: <honest number or how it will be obtained>
- Recovery: <test>
- Interactive surface: <library> — editing model — e2e flows
- Sources: <source> — paths <1, 2, 3> — yield <x / unknown> — verification — degradation
- Added to the card: <components added>

## Shared additions
Gates and tricks that every finalist should carry (copied into each card).
```

Update every card in `03-candidates.md` with the added components (mark them `[craft]`). Record with `progress.py impact craft --added "<components>" --note ".."`.

## 7. What changes downstream

- Screening and the panel score the cards **with** the craft additions; a candidate whose core bet contradicts a probe fact is cut.
- The rubric anchors for reliability and quality at level 4–5 require named gates and a measured comparison with the simple alternative (`rubric.md`).
- The prototype measures the leader, P+ **and** the simple alternative; a complex component that loses ships behind a switch (`prototype.md`).
- The report has a section "What a jury will ask" with the honest answers (`report-template.md`).
