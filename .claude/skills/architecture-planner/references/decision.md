# Decision: ADR and build plan

After the report, the user picks an option (or a combination). This stage turns the choice into two durable files that let the team start building immediately.

Ask the user which option they choose, and whether they want to change anything in it. If they choose a mix of options, describe the mix in 3–5 lines and confirm before writing.

**Autopilot:** the choice is a hard stop. Write the ADR for the top recommendation with `Status: proposed` (not `accepted`), mark `BUILD-PLAN.md` as a draft, do not copy anything into `docs/adr/`, and list "accept or change the ADR" first in the report's "Decided without you" block.

## 1. `ADR-0001-<short-title>.md` (MADR-style)

If the repository already has a `docs/adr/` or `docs/decisions/` folder, follow its numbering (next number after the highest) and format. Otherwise save in the working directory and offer to copy it to `docs/adr/` — writing into the repo needs the user's yes.

```markdown
# ADR-0001: <decision in one line>

Status: accepted | proposed (autopilot) · Date: <YYYY-MM-DD> · Deciders: <names> · Supersedes: <ADR number or none>

## Context
The problem, key constraints and criterion weights from the brief (5–10 lines). Link 01-task-brief.md.

## Decision
The chosen architecture in 3–6 lines and its core bet, with a Mermaid C4-style container diagram.

## Options considered
| Option | Total, % (score.py) | Win share | Why not chosen |
Every final option plus the most relevant rejected ones. One line each.

## Consequences
Good: what this makes easy. Bad: what this makes hard or what we accept as a risk.
Mitigations for the main risks.

## Validation
Which scenarios must pass for this decision to stand; what result would make us revisit it.
```

Keep it short: an ADR records the decision and its reasons, not the whole analysis — link the report for that.

## 2. `BUILD-PLAN.md`

```markdown
# Build plan: <option name>

## Milestones
M0 — walking skeleton: end-to-end path with stubs, passes 1 typical scenario. (Always first.)
M1 — core: passes all critical scenarios.
M2 — coverage: passes ≥ N of all scenarios; error handling.
M3 — demo polish (hackathon) or hardening (product).

## Tasks
| # | Task | Milestone | Depends on | Scenarios it serves | Estimate | Owner |

Each task: small enough for one person in ≤ 2 hours (hackathon) or ≤ 1 day (product), with a done-criterion.

## Eval
How to run 02-scenarios.md against the system after each milestone (script or LLM-judge rubric), and the target pass rate per milestone.

## Risks to check first
The riskiest assumptions from the ADR, each turned into a task in M0 or M1.

## Time budget
Milestones against the deadline, with a cut line: what gets dropped first if time runs out.
```

Rules:
- Every task links to at least one scenario or to a risk; tasks that serve neither are cut or justified.
- In team mode, propose owners by splitting tasks so people can work in parallel with minimal dependencies.
- If a prototype from stage 13 exists for the chosen option, start M0 from it and say which parts are reused.
