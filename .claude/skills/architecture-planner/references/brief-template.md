# Task brief template — 01-task-brief.md

Write it in the user's language. In team mode members decide their own questions from this file, so it must stand on its own.

```markdown
# Task brief: <task name>

Mode: full | express · Autopilot: no | yes (budget: <USD or none>) · Date: <YYYY-MM-DD>

## Goal and success
What the system must do, for whom, and what counts as success (judging criteria verbatim for a hackathon).

## Inputs and outputs
Data in (format, volume, examples in the folder), results out.

## Actions in the outside world
What the system will do beyond answering: write, send, pay, delete, book, post… For each: reversible or not, whether a human confirms it, which inputs are untrusted (user text, web pages, emails, tool outputs).
None → "Read-only".

## What the judges will run
Exact evaluation conditions: the checker, the data it sees, the signals available at evaluation time, the metric. "Not stated" if unknown — then it is a grill question.

## What is missing in the input
Data or access the task relies on but the folder does not contain (e.g. telemetry referenced by the spec but absent). From `00-inventory.md` and `00b-probe.md`.

## Facts from the probe
The 3–5 facts from `00b-probe.md` that constrain every design, with numbers. The trivial answer and its metric.

## Hard constraints   (violation = rejection)
Deadline, budget, stack or models fixed by the user, privacy, hardware, latency ceiling…

## Soft preferences

## Team
Size, skills, what they have built before, what they want to avoid.

## Criterion weights   (swing weighting; sum = 100)
| Criterion | Weight | Swing points | Why |
|---|---|---|---|
| Result quality | | | |
| Reliability | | | |
| Speed of implementation | | | |
| Cost | | | |
| Latency | | | |
| Debuggability | | | |
| Evaluability | | | |
| Extensibility | | | |
| Demo impact | | | |
| Safety / blast radius | | | |

## Anchors   (absolute; one row per criterion with weight > 0; same text as scores.json)
| Criterion | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|
| Latency | p95 ≤ <target/2> | p95 ≤ <target> | ≤ 1.5 × target | ≤ 2 × target | > 2 × target |
| … | | | | | |

## Confirmed assumptions

## Assumptions taken on autopilot — please review   (autopilot only)
| # | Question | Assumed answer | Status | Affects |
| 1 | … | … | assumed | weights / scenario S4 / criterion latency |

## Open risks
```

Defaults and anchor templates: `rubric.md`. Weights from swing answers: `score.py weights --swing …`.
