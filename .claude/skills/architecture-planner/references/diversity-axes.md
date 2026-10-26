# Diversity axes and generation

Contents: Generation procedure · Axes 1–9 · Checking diversity

## Generation procedure

Generation runs in **two waves** and is **hierarchical**, with a stop rule instead of a quota.

**Wave A — blind (stage 5, before prior art).** Do not read, search for or recall `02b-prior-art.md` (it does not exist yet; in team mode, wave A tasks never receive it). This is why wave A comes before prior art: once found projects are in context they cannot be unseen, and candidates drift towards them.
1. **Families.** Name 4–6 families by axis 1 (control flow; for non-agent systems, the adapted axis), each in 2–3 lines: what decides the next step and why it could suit this task.
2. **Variants.** Inside the families, build variants by varying the other axes. Full: 7–8 candidates; express: 4. Include the minimal baseline and at least one non-agentic and one radical option here.

**Wave B — informed (stage 7, after prior art).** Read `02b-prior-art.md`. Add the remaining candidates, including at least two built mainly from ready-made parts (axis 9) and, if there is existing code, "extend the current architecture with the smallest change". New families are allowed.

**Stop rule.** After each new candidate, run `score.py diversity --md 03-candidates.md`. Stop generating when either
- the new candidate differs from its nearest neighbour on fewer than 3 axes and two further attempts in a row also fail to (discard those near-duplicates), or
- the ceiling is reached: full 15, express 8.

**Floor:** full 8, express 5. If the task genuinely has fewer distinct designs, stop below the floor and say so in `03-candidates.md`, with the reason (e.g. "a fixed vendor API leaves only the orchestration open"). A narrow solution space is a valid finding, not a failure; padding it with straw men wastes every later stage.

**Card per candidate** (~10 lines): id, name and one-line essence; **wave (A/B)**; family; value on each axis; components; data flow; **Actions & permissions** (what it can do in the outside world, which tools with which rights, what is reversible, where confirmations sit); core bet ("this wins if …"); most obvious risk. Register with `progress.py cand add C1 "<name>" "<idea>" --wave A`.

**`03-candidates.md` structure:** Families → candidate cards → candidate × axis matrix (first column `Candidate`, one column per axis) → why generation stopped (stop rule or ceiling, with counts per wave) → the diversity report from `score.py diversity`.

Selection counts follow the actual number: screening keeps about half (never fewer than 3), the final selection is at most 5 (express: 3).

## Axes

Use these axes to make candidates differ in substance. For non-agent systems (services, data pipelines), adapt them: replace "control flow" with the interaction style between components, "model strategy" with the core processing technology, and so on.

## 1. Control flow (who decides what happens next)
- Deterministic pipeline / workflow (LLM only as a function inside steps)
- Single agent in a loop (ReAct / tool-calling loop)
- Plan-and-execute (plan first, then execute; with or without replanning)
- Graph / explicit state machine (fixed transitions, LLM picks the branch)
- Hierarchy: orchestrator + specialised sub-agents
- Peer agents (debate, voting, blackboard)
- Search over alternatives (tree search, beam, best-of-N with a verifier)
- Event-driven / reactive (agents react to events, task queue)

## 2. Action form
- Structured tool calls (JSON)
- Code as action (agent writes and executes code)
- Generates a config / DSL that a deterministic engine executes
- Direct answer, no tools

## 3. Knowledge and context
- Everything in the prompt (long context)
- RAG (vector / hybrid / knowledge graph)
- Agentic search (agent explores files / web itself)
- Precomputed structures (indexes, summaries, caches)

## 4. Memory and state
- Stateless (every request from scratch)
- Sliding window / history compression
- Explicit external memory (notes, scratchpad, database)
- Accumulated memory (experience from past runs, skill library)

## 5. Quality control
- None
- Self-check by the same model
- Separate verifier / critic LLM
- Deterministic checks (tests, schemas, rules, execution)
- Human in the loop

## 6. Model strategy
- One strong model everywhere
- Cascade / routing (cheap model first, strong model when hard)
- Specialised models per role
- Fine-tuned small model + strong model for hard cases
- No LLM at runtime for part of the task (classic ML, rules)

## 7. Execution and deployment
- Synchronous, single process
- Asynchronous / parallel (fan-out, map-reduce)
- Long-running background agents
- Offline preprocessing + lightweight online path

## 8. User interaction
- Fully autonomous
- Confirmation of key actions
- Collaborative (user edits the plan or result)

## 9. Build vs reuse
- Built from scratch on a model API
- Built on an agent / app framework
- Assembled mainly from existing services and APIs (glue code)
- Forked from an open-source project or a similar hackathon project found in `02b-prior-art.md`

## Checking diversity
Any two candidates must differ on at least 3 of the 9 axes; `score.py diversity` checks every pair from the matrix and lists the ones that do not. To fix a near-duplicate, change axis 1 (control flow) or axis 2 (action form), which change behaviour the most, or drop it.


## 10. How quality is proven (ML, data, estimation tasks)
- Offline score only (one split)
- Session/event split + locked test + bootstrap against the simple baseline
- Online check on the judge's protocol (official checker in CI)
- Human-in-the-loop labelling of a small gold set
- Shadow mode: the new part runs beside the simple one and only logs

## 11. Source access strategy (systems that depend on external sources)
- Official API / open dataset / registry dump
- Structured data embedded in pages (JSON-LD, internal JSON endpoints)
- Plain HTTP with a browser TLS fingerprint
- Real browser (headless → non-headless + virtual display)
- Several search engines or providers with fallback and voting
- Partner feeds / aggregators, verified against a primary source

Two candidates that differ only in axes 10–11 are still near-duplicates by architecture; use these axes to enrich candidates, and to keep at least one option per family strong on them.
