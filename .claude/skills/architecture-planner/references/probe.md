# Probe — touch the real data and the real sources (stage 1)

Contents: 1. Why · 2. Time box · 3. Data probe · 4. Source probe · 4a. Domain primer · 5. Output · 6. Rules

## 1. Why

In earlier runs every decisive finding came from a measurement, and every one came late (stage 8):
- "the winner was already in the history in 95 % of lots" — the new-supplier search could not move recall;
- "on lab + analyser data nobody beats climatology for the exceedance" — the forecast had to change its role;
- "Ozon redirects to a challenge, WB returns 403, DuckDuckGo HTML is challenged on the first request";
- "web search for suppliers returns aggregators, not companies with a tax id".

Teams that beat the skill found such facts in their first hour, by hand, and built the architecture around them (unit traps, telemetry markers, sensor silence, non-headless browsers). A design made before touching the data optimises an imagined task. The probe moves those facts to the start, so grill, scenarios and both waves use them.

## 2. Time box

Full: 15–30 minutes. Express: 10 minutes. The probe answers "what is true about the data and sources", never "which design" — no candidate names here.

Local only. No paid API calls without `--budget`. No more than a handful of requests per external source (be polite: no load, no bypass attempts, respect robots.txt and terms).

## 3. Data probe (when the folder has data)

For each dataset (if the folder has many, the ones the target depends on):
1. **Shape:** rows, columns, time span, keys, join coverage between tables (what share of rows find a partner).
2. **Target:** distribution, base rate, how often the "event" happens, how it changes over time.
3. **Traps:** units (km/h vs m/s), timestamps (late, out of order, time zones), gaps and silences (how long a sensor can be silent), saturation values, sentinel markers (e.g. a constant like 307.0 that means "no data"), duplicates, manual-entry outliers, leakage (fields known only after the outcome).
4. **The trivial answer:** how well does the simplest rule do on a time-based split — persistence, frequency, "same as last time", the mean of two redundant sensors? Write the number. This is the bar every candidate must clear, and often the most important number of the run.
5. **Is the answer already in the data?** e.g. share of test cases whose answer appears in the history; share explained by one key (same customer, same route).
6. **What the judges will run:** if the folder has a checker script, an evaluation protocol or a sample of the judge's input, read it and note the exact conditions (what signals exist at evaluation time — e.g. GNSS only at the start). Numbers obtained under easier conditions do not count.

Use whatever runs locally (pandas, DuckDB via `uv run --with duckdb`). If reading XLSX fails, create a throwaway venv (`uv venv` + pandas/openpyxl) inside the working directory.

## 4. Source probe (when the system depends on external sources or APIs)

For each source named in the task or obviously needed (marketplaces, registries, search engines, public APIs):
1. One or two real requests: status code, redirect, challenge page, format (HTML, JSON, embedded JSON), rate-limit headers, whether the needed field is present.
2. Terms of use and an official API, if any.
3. What the answer actually contains for a typical query (e.g. companies or aggregators? prices or "from …"?).

Record failures precisely ("403 on the second request from this IP", "JS challenge"). The environment's IP and region matter; write them down.

## 4a. Domain primer (when the task sits on deep domain knowledge)

Physics, chemistry, process engineering, medicine, law, finance, railway dynamics… Teams lose most time where the domain is hard to read ("hard to understand the gases and the fine points of the chemistry" — feedback on a refinery-advisor task). Build a short primer **before** grill, so questions, scenarios and candidates use the right physics:
1. **Glossary:** every domain term in the input, in one line each, with units.
2. **Mechanisms:** the 5–10 cause→effect relations the system must respect (e.g. "higher reactor temperature → lower product sulphur, faster catalyst ageing"; "blend property X mixes linearly by volume, Y does not — use the blending index"). State which are linear, which have delays, which have hard limits.
3. **How practitioners work:** what an operator / analyst actually does today, which decisions they make, what they would never accept from a tool.
4. **Norms and limits:** standards and regulations that fix thresholds (GOST, EN, sector rules), with numbers.
5. **Fine points and traps:** measurement methods that differ between lab and analyser, lag of lab results, mixing laws, sign conventions, regime changes.
6. **Sources:** textbooks, standards, review papers, vendor manuals — official docs first; write each fact in your own words with the source.

Save `00c-domain.md` (≤ 2 pages). Time box: full 15–25 minutes, express 10. If the domain is deep, the critic panel gets a **domain expert** lens (`critic.md`), and the craft pass checks every candidate against the mechanisms list.

## 5. Output — `00b-probe.md`

```markdown
# Probe
Environment: date, IP region, tools used, minutes spent.

## Facts that constrain the design
1. <fact> — <number> — <how measured> (query / script path)
...

## The trivial answer
<rule> → <metric> on <split>. This is the bar.

## Traps found
| Trap | Where | Evidence | Consequence for any design |

## Source behaviour
| Source | Request | Result | Usable? |

## What we could not check
<item> — why — what would check it.
```

Save the scripts under `00b-probe/`. Record impact with `progress.py impact probe --note "<the 1–3 facts that matter most>"`.

## 6. Rules

- Facts, not designs. "Registry search returns companies with tax ids, web search returns aggregators" is a fact; "use registries" is a design and waits for stage 4.
- Grill does not ask what the probe answered.
- Scenarios (stage 4) include at least one scenario built on each trap found.
- Both waves read `00b-probe.md`. It is not prior art: wave A may read it.
- The critic panel gets the probe facts as conditions; a candidate that ignores a trap scores as if it fails the trap.
