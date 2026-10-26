#!/usr/bin/env python3
"""Progress tracker for the architecture-planner skill.

Keeps state in <workdir>/progress.json and re-renders two views on every call:
  - progress.html : self-contained dashboard, auto-refreshes every 4 s
  - PROGRESS.md   : plain-text view for chat / terminals

Stdlib only. Every command re-renders both views.

Usage (run from anywhere). Pass --workdir <dir> on every call (works the same in
bash, zsh and PowerShell); it falls back to $ARCH_WORKDIR, then ./_arch.
Use `python` or, on Windows if that is not found, `py`.
  progress.py init --task "Task name" [--lang ru] [--mode full|express] [--autopilot yes|no] [--budget USD]
  progress.py autopilot yes|no [--budget USD]     # switch mid-run ("I'm leaving, decide yourself")
  progress.py stage <id> start|done|skip [--note "..."]
  progress.py now "What I'm doing right now"
  progress.py next "Planned step 1" "Planned step 2" ...
  progress.py cand add <id> "Name" "One-line idea" [--wave A|B]
  progress.py cand set <id> <status> [--score 72.5] [--reason "..."] [--wave A|B]
        --score must be a number 0..100 (normally written by score.py aggregate)
        status: proposed | screened_out | finalist | selected
  progress.py q add <id> "Research question" [--cand C3,C7] [--voi 0.12]
  progress.py q set <id> open|searching|answered|not_found [--answer "..."] [--confidence high|medium|low] [--voi 0.12]
        questions are listed by VOI (from score.py voi), highest first
  progress.py artifact <path> [--label "..."]
  progress.py wait "What you need from the user"   # highlighted until next stage/now
  progress.py proto add <cand-id>
  progress.py proto set <cand-id> planned|running|done|failed [--result "8/10 scenarios, p50 3.1 s"]
  progress.py status           # where a run stopped; use it to resume
  progress.py impact <stage> [--leader-before C1 --leader-after C4] [--added C9,gate:two-keys]
               [--removed C2] [--final yes|no|unknown] [--minutes 12] [--note ".."]
                               # what the stage changed -> progress.json "impact" + STAGE-IMPACT.md
  progress.py log "Message"      # "AUTOPILOT: <decision> — <why>" is also appended to AUTOPILOT-DECISIONS.md
  progress.py render
  progress.py todos            # print the task list for TodoWrite / task tools (JSON)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys

STAGES = [
    ("read", "Read the input folder"),
    ("probe", "Probe live data and sources"),
    ("grill", "Clarify task conditions (grill)"),
    ("brief", "Write the task brief"),
    ("scenarios", "Write test scenarios"),
    ("wavea", "Generate wave A (blind to prior art)"),
    ("priorart", "Find prior art and ready-made parts"),
    ("generate", "Generate wave B + diversity check"),
    ("craft", "Craft and safety-gates pass"),
    ("screen", "Critic screening + your review"),
    ("research", "Targeted research"),
    ("critique", "Critic panel: deep review"),
    ("hybrid", "Build and review a hybrid"),
    ("prototype", "Prototype the top options"),
    ("select", "Select the final options"),
    ("report", "Write the final report"),
    ("decide", "Record the decision (ADR) and build plan"),
]
STAGE_IDS = [s[0] for s in STAGES]


def now():
    return dt.datetime.now().strftime("%H:%M:%S")


def wd(args):
    return os.path.abspath(args.workdir or os.environ.get("ARCH_WORKDIR") or "_arch")


def load(path):
    p = os.path.join(path, "progress.json")
    if not os.path.exists(p):
        sys.exit(f"No progress.json in {path}. Run `init` first.")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(path, st):
    os.makedirs(path, exist_ok=True)
    st["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(path, "progress.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    render(path, st)


def log(st, msg):
    st["log"].insert(0, {"t": now(), "msg": msg})
    st["log"] = st["log"][:200]


# ---------------------------------------------------------------- rendering

CSS = """
:root{--paper:#EEF1F4;--ink:#1C2733;--line:#C9D1DA;--blue:#2F5D8A;--done:#2E7D6B;
--active:#B7791F;--muted:#6B7785;--out:#A3ACB6;--card:#F8FAFB}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.55 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;flex-wrap:wrap;gap:12px 32px;align-items:end;justify-content:space-between;
border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
h1{font:600 26px/1.2 "IBM Plex Serif",Georgia,serif;margin:0}
.sub{color:var(--muted);font-size:13px}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;width:240px}
.bar i{display:block;height:100%;background:var(--blue)}
.top{display:grid;grid-template-columns:1fr 1fr;gap:20px 28px;margin-bottom:26px}
@media(max-width:860px){.top{grid-template-columns:1fr}}
.scroll{overflow-x:auto}
.grid{display:grid;grid-template-columns:330px 1fr;gap:28px}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:2px 10px;border-radius:10px;background:#7A3E9D;color:#fff;margin-left:8px;vertical-align:middle}
h2{font:600 16px/1.3 "IBM Plex Serif",Georgia,serif;margin:0 0 10px}
section{margin-bottom:26px}
ol.track{list-style:none;margin:0;padding:0;position:relative}
ol.track li{position:relative;padding:0 0 16px 34px}
ol.track li:before{content:"";position:absolute;left:10px;top:22px;bottom:0;width:2px;background:var(--line)}
ol.track li:last-child:before{display:none}
.dot{position:absolute;left:0;top:2px;width:22px;height:22px;border-radius:50%;
border:2px solid var(--line);background:var(--paper);font-size:11px;line-height:18px;text-align:center;color:var(--muted)}
li.done .dot{background:var(--done);border-color:var(--done);color:#fff}
li.active .dot{border-color:var(--active);color:var(--active);box-shadow:0 0 0 4px rgba(183,121,31,.18)}
li.skip .dot{border-style:dashed}
li.done .name{color:var(--ink)} li.todo .name{color:var(--muted)}
li.active .name{font-weight:600}
.note{font-size:13px;color:var(--muted);font-weight:400}
.wait{grid-column:1/-1;border:2px solid var(--active);background:#FBF3E4;padding:10px 14px}
.wait b{color:var(--active)}
.now{margin:0;border-left:3px solid var(--active);background:var(--card);padding:10px 14px;margin-bottom:12px}
.now b{display:block;font-size:12px;color:var(--active);font-weight:600}
ul.next{margin:0;padding-left:18px;color:var(--ink)}
.funnel{display:flex;align-items:end;gap:10px;margin:4px 0 14px}
.funnel div{text-align:center}
.funnel strong{display:block;font:600 34px/1 "IBM Plex Serif",Georgia,serif}
.funnel span{font-size:12px;color:var(--muted)}
.funnel em{font-style:normal;color:var(--line);font-size:22px;padding-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:14px;background:var(--card)}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;font-size:12px;color:var(--muted)}
tr.screened_out td{color:var(--out)} tr.screened_out td.n>span{text-decoration:line-through}
tr.selected td.n{font-weight:600}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--line);white-space:nowrap}
.tag.selected{background:var(--done);border-color:var(--done);color:#fff}
.tag.finalist{border-color:var(--blue);color:var(--blue)}
.tag.done,.tag.answered{border-color:var(--done);color:var(--done)}
.tag.running,.tag.searching{border-color:var(--active);color:var(--active)}
.tag.not_found{border-style:dashed;color:var(--muted)}
.log{font-size:13px;max-height:340px;overflow:auto;background:var(--card);padding:8px 12px;border:1px solid var(--line)}
.log div{padding:2px 0} .log time{color:var(--muted);margin-right:8px;font-variant-numeric:tabular-nums}
a{color:var(--blue)}
.empty{color:var(--muted);font-size:13px;font-style:italic}
"""

STATUS_LABEL = {
    "proposed": "proposed", "screened_out": "screened out", "finalist": "finalist",
    "selected": "selected", "open": "open", "searching": "searching",
    "answered": "answered", "not_found": "not found",
}


def qkey(kv):
    """Questions: highest VOI first, then natural id order."""
    voi = kv[1].get("voi")
    return (-(voi if isinstance(voi, (int, float)) else -1), natkey(kv[0]))


def parse_score(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        sys.exit(f"--score must be a number from 0 to 100, got {v!r}")
    if not 0 <= x <= 100:
        sys.exit(f"--score must be between 0 and 100, got {x:g}")
    return round(x, 1)


def parse_voi(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        sys.exit(f"--voi must be a number from 0 to 1, got {v!r}")
    if not 0 <= x <= 1:
        sys.exit(f"--voi must be between 0 and 1, got {x:g}")
    return x


def natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def e(s):
    return html.escape(str(s or ""))


def render(path, st):
    stages = st["stages"]
    done_n = sum(1 for s in STAGE_IDS if stages[s]["status"] in ("done", "skip"))
    pct = int(done_n / len(STAGE_IDS) * 100)
    cands = st["candidates"]
    n_all = len(cands)
    n_fin = sum(1 for c in cands.values() if c["status"] in ("finalist", "selected"))
    n_sel = sum(1 for c in cands.values() if c["status"] == "selected")

    track = []
    for i, (sid, name) in enumerate(STAGES):
        s = stages[sid]
        cls = {"done": "done", "active": "active", "skip": "skip"}.get(s["status"], "todo")
        mark = "✓" if cls == "done" else ("–" if cls == "skip" else str(i))
        note = f'<div class="note">{e(s.get("note"))}</div>' if s.get("note") else ""
        when = f' <span class="note">{e(s.get("t"))}</span>' if s.get("t") else ""
        track.append(f'<li class="{cls}"><span class="dot">{mark}</span>'
                     f'<span class="name">{e(name)}</span>{when}{note}</li>')

    nxt = "".join(f"<li>{e(x)}</li>" for x in st["next"]) or '<li class="empty">nothing queued</li>'

    rows = []
    order = {"selected": 0, "finalist": 1, "proposed": 2, "screened_out": 3}
    for cid, c in sorted(cands.items(), key=lambda kv: (order.get(kv[1]["status"], 9), natkey(kv[0]))):
        score = e(c.get("score")) if c.get("score") not in (None, "") else "—"
        rows.append(f'<tr class="{c["status"]}"><td>{e(cid)}</td><td class="n"><span>{e(c["name"])}</span>'
                    f'<div class="note">{e(c.get("idea"))}</div></td>'
                    f'<td><span class="tag {c["status"]}">{STATUS_LABEL[c["status"]]}</span></td>'
                    f'<td>{e(c.get("wave")) or "—"}</td><td>{score}</td><td class="note">{e(c.get("reason"))}</td></tr>')
    cand_html = (f'<table><tr><th>ID</th><th>Architecture</th><th>Status</th><th>Wave</th><th>Score</th><th>Reason</th></tr>'
                 f'{"".join(rows)}</table>') if rows else '<p class="empty">Candidates appear once generation starts.</p>'

    qrows = []
    for qid, q in sorted(st["questions"].items(), key=qkey):
        ans = e(q.get("answer"))
        voi_s = "—" if q.get("voi") is None else f"{q['voi']:.2f}"
        conf = f' <span class="note">({e(q["confidence"])})</span>' if q.get("confidence") else ""
        qrows.append(f'<tr><td>{e(qid)}</td><td>{e(q["text"])}<div class="note">for {e(q.get("cand"))}</div></td>'
                     f'<td>{voi_s}</td>'
                     f'<td><span class="tag {q["status"]}">{STATUS_LABEL[q["status"]]}</span></td>'
                     f'<td>{ans}{conf}</td></tr>')
    q_html = (f'<table><tr><th>ID</th><th>Question</th><th>VOI</th><th>Status</th><th>Finding</th></tr>{"".join(qrows)}</table>'
              if qrows else '<p class="empty">Research questions appear after screening.</p>')

    arts = "".join(f'<li><a href="{e(os.path.relpath(a["path"], path).replace(os.sep, "/"))}">{e(a["label"])}</a></li>'
                   for a in st["artifacts"]) or '<li class="empty">none yet</li>'
    logs = "".join(f'<div><time>{e(x["t"])}</time>{e(x["msg"])}</div>' for x in st["log"])

    wait_html = (f'<div class="wait"><b>Waiting for you:</b> {e(st["waiting"])}</div>' if st.get("waiting") else "")
    prow = "".join(f'<tr><td>{e(cid)}</td><td>{e(cands.get(cid, {}).get("name", ""))}</td>'
                   f'<td><span class="tag {e(pr["status"])}">{e(pr["status"])}</span></td><td>{e(pr.get("result"))}</td></tr>'
                   for cid, pr in sorted(st.get("protos", {}).items(), key=lambda kv: natkey(kv[0])))
    proto_html = (f'<section><h2>Prototypes</h2><div class="scroll"><table><tr><th>ID</th><th>Architecture</th>'
                  f'<th>Status</th><th>Measured on scenarios</th></tr>{prow}</table></div></section>' if prow else "")
    badge = ""
    if st.get("autopilot"):
        budget = st.get("budget")
        badge = (f'<span class="badge">AUTOPILOT · '
                 f'{"budget $" + format(budget, "g") if budget is not None else "no prototype budget"}</span>')
    final = all(stages[s]["status"] in ("done", "skip") for s in STAGE_IDS)
    refresh = "" if final else '<meta http-equiv="refresh" content="4">'

    doc = f"""<!doctype html><html lang="{e(st.get('lang','en'))}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>{e(st['task'])} · architecture planner</title><style>{CSS}</style></head><body><main>
<header><div><h1>{e(st['task'])}{badge}</h1>
<div class="sub">Architecture planner · {e(st.get("mode","full"))} mode · started {e(st['started'][11:16])} · updated {e(st['updated'][11:19])}</div></div>
<div><div class="sub">{done_n} of {len(STAGE_IDS)} stages · {pct}%</div><div class="bar"><i style="width:{pct}%"></i></div></div></header>
<div class="top">{wait_html}<div class="now"><b>Now</b>{e(st['now']) or '—'}</div>
<div><h2>Up next</h2><ul class="next">{nxt}</ul></div></div>
<div class="grid"><div>
<section><h2>Pipeline</h2><ol class="track">{''.join(track)}</ol></section>
<section><h2>Files</h2><ul>{arts}</ul></section>
</div><div>
<section><h2>Candidates</h2>
<div class="funnel"><div><strong>{n_all}</strong><span>generated</span></div><em>›</em>
<div><strong>{n_fin}</strong><span>finalists</span></div><em>›</em>
<div><strong>{n_sel}</strong><span>selected</span></div></div><div class="scroll">{cand_html}</div></section>
<section><h2>Research</h2><div class="scroll">{q_html}</div></section>
{proto_html}
<section><h2>Activity</h2><div class="log">{logs}</div></section>
</div></div></main></body></html>"""
    with open(os.path.join(path, "progress.html"), "w", encoding="utf-8") as f:
        f.write(doc)

    icon = {"done": "✅", "active": "▶️", "skip": "⏭️", "todo": "⏳"}
    md = [f"# {st['task']} — progress ({pct}%)" + (" — AUTOPILOT" if st.get("autopilot") else ""), ""]
    for i, (sid, name) in enumerate(STAGES):
        s = stages[sid]
        md.append(f"{icon.get(s['status'],'⏳')} {i}. {name}" + (f" — {s['note']}" if s.get("note") else ""))
    if st.get("waiting"):
        md += ["", f"**⏸ Waiting for you:** {st['waiting']}"]
    md += ["", f"**Now:** {st['now'] or '—'}", "", "**Next:**"] + [f"- {x}" for x in st["next"]]
    if cands:
        md += ["", f"**Candidates:** {n_all} → {n_fin} → {n_sel}"]
    with open(os.path.join(path, "PROGRESS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


def print_status(st, path):
    nxt = next((sid for sid in STAGE_IDS if st["stages"][sid]["status"] not in ("done", "skip")), None)
    files = [os.path.basename(x["path"]) for x in st["artifacts"]]
    print(json.dumps({
        "task": st["task"], "mode": st["mode"], "autopilot": st.get("autopilot", False),
        "budget": st.get("budget"),
        "resume_from_stage": nxt,
        "stage_states": {sid: st["stages"][sid]["status"] for sid in STAGE_IDS},
        "waiting_for_user": st["waiting"],
        "candidates": {cid: c["status"] for cid, c in st["candidates"].items()},
        "open_questions": [qid for qid, q in st["questions"].items() if q["status"] in ("open", "searching")],
        "saved_files": files, "workdir": path,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- stage impact

def write_impact_md(path, st):
    """STAGE-IMPACT.md: what each stage changed. Used to judge which stages earn their cost."""
    rows = ["# Stage impact", "",
            "What each stage changed in the decision. `reached_final` = something from this stage is in the selected architecture.", "",
            "| Stage | Min | Leader before → after | Added | Removed | Reached final | Note |",
            "|---|---|---|---|---|---|---|"]
    for r in st.get("impact", []):
        lead = (r["leader_before"] + " → " + r["leader_after"]) if r["leader_before"] or r["leader_after"] else ""
        if r["leader_changed"]:
            lead = "**" + lead + "**"
        mins = "" if r.get("minutes") is None else f"{r['minutes']:g}"
        rows.append(f"| {r['stage']} | {mins} | {lead} | {', '.join(r['added'])} | {', '.join(r['removed'])} | "
                    f"{r['reached_final']} | {r['note'].replace('|', '/')} |")
    with open(os.path.join(path, "STAGE-IMPACT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


# ---------------------------------------------------------------- task list

ACTIVE_FORM = {
    "read": "Reading the input folder",
    "probe": "Probing live data and sources",
    "grill": "Clarifying task conditions",
    "brief": "Writing the task brief",
    "scenarios": "Writing test scenarios",
    "wavea": "Generating wave A",
    "priorart": "Searching for prior art",
    "generate": "Generating wave B",
    "craft": "Running the craft and safety-gates pass",
    "screen": "Running critic screening",
    "research": "Researching open questions",
    "critique": "Running the critic panel",
    "hybrid": "Building the hybrid option",
    "prototype": "Prototyping top options",
    "select": "Selecting the final options",
    "report": "Writing the final report",
    "decide": "Writing the ADR and build plan",
}


def build_todos(st):
    """Task list mirroring the pipeline stages, plus live sub-items for the
    research, critique and prototype stages so the list shows real progress."""
    todos = []
    stages = st["stages"]
    for i, (sid, name) in enumerate(STAGES):
        s = stages[sid]
        status = {"done": "completed", "skip": "completed", "active": "in_progress"}.get(s["status"], "pending")
        label = f"{i}. {name}" + (f" ({s['note']})" if s.get("note") and status == "completed" else "")
        if status == "in_progress" and st.get("waiting"):
            label += " (waiting for you)"
        todos.append({"content": label, "status": status, "activeForm": ACTIVE_FORM[sid]})

        if sid == "research" and st["questions"] and s["status"] != "done":
            for qid, q in sorted(st["questions"].items(), key=qkey):
                qs = {"answered": "completed", "not_found": "completed",
                      "searching": "in_progress"}.get(q["status"], "pending")
                tail = " (not found)" if q["status"] == "not_found" else ""
                todos.append({"content": f"   {qid}: {q['text']}{tail}", "status": qs,
                              "activeForm": f"Researching {qid}"})

        if sid == "prototype" and s["status"] == "active":
            for cid, pr in sorted(st["protos"].items(), key=lambda kv: natkey(kv[0])):
                ps = {"done": "completed", "failed": "completed", "running": "in_progress"}.get(pr["status"], "pending")
                tail = f" ({pr['result']})" if pr.get("result") else ""
                todos.append({"content": f"   Prototype {cid}{tail}", "status": ps,
                              "activeForm": f"Prototyping {cid}"})

        if sid == "critique" and s["status"] == "active":
            fins = [(cid, c) for cid, c in st["candidates"].items() if c["status"] in ("finalist", "selected")]
            for cid, c in sorted(fins, key=lambda kv: natkey(kv[0])):
                scored = c.get("score") not in (None, "")
                todos.append({"content": f"   Critique {cid}: {c['name']}" + (f" ({c['score']})" if scored else ""),
                              "status": "completed" if scored else "pending",
                              "activeForm": f"Critiquing {cid}"})

    # Task tools expect a single in_progress item: the active stage keeps it,
    # and a sub-item being worked on is labelled instead.
    seen = False
    for t in todos:
        if t["status"] == "in_progress":
            if seen:
                t["status"] = "pending"
                t["content"] += " (in progress)"
            seen = True
    return todos


# ---------------------------------------------------------------- commands

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--task", required=True); p.add_argument("--lang", default="en")
    p.add_argument("--mode", choices=["full", "express"], default="full")
    p.add_argument("--autopilot", choices=["yes", "no"], default="no"); p.add_argument("--budget", type=float)
    p = sub.add_parser("autopilot"); p.add_argument("value", choices=["yes", "no"]); p.add_argument("--budget", type=float)
    p = sub.add_parser("stage"); p.add_argument("id", choices=STAGE_IDS)
    p.add_argument("action", choices=["start", "done", "skip"]); p.add_argument("--note")
    p = sub.add_parser("now"); p.add_argument("text")
    p = sub.add_parser("next"); p.add_argument("items", nargs="*")
    p = sub.add_parser("cand"); p.add_argument("action", choices=["add", "set"]); p.add_argument("id")
    p.add_argument("rest", nargs="*"); p.add_argument("--score"); p.add_argument("--reason")
    p.add_argument("--wave", choices=["A", "B"])
    p = sub.add_parser("q"); p.add_argument("action", choices=["add", "set"]); p.add_argument("id")
    p.add_argument("rest", nargs="*"); p.add_argument("--cand"); p.add_argument("--answer"); p.add_argument("--confidence")
    p.add_argument("--voi")
    p = sub.add_parser("artifact"); p.add_argument("path"); p.add_argument("--label")
    p = sub.add_parser("log"); p.add_argument("msg")
    sub.add_parser("render")
    sub.add_parser("todos")
    sub.add_parser("status")
    p = sub.add_parser("wait"); p.add_argument("text")
    p = sub.add_parser("proto"); p.add_argument("action", choices=["add", "set"]); p.add_argument("id")
    p.add_argument("rest", nargs="*"); p.add_argument("--result")
    p = sub.add_parser("impact"); p.add_argument("id", choices=STAGE_IDS)
    p.add_argument("--leader-before", default=""); p.add_argument("--leader-after", default="")
    p.add_argument("--added", default="", help="candidates/components added, comma-separated")
    p.add_argument("--removed", default="", help="candidates/components removed, comma-separated")
    p.add_argument("--final", choices=["yes", "no", "unknown"], default="unknown",
                   help="did something from this stage reach the selected architecture")
    p.add_argument("--minutes", type=float); p.add_argument("--note", default="")
    a = ap.parse_args()
    path = wd(a)

    if a.cmd == "init":
        st = {"task": a.task, "lang": a.lang, "mode": a.mode, "waiting": "", "protos": {},
              "autopilot": a.autopilot == "yes", "budget": a.budget, "started": dt.datetime.now().isoformat(timespec="seconds"),
              "stages": {s: {"status": "todo"} for s in STAGE_IDS}, "now": "", "next": [],
              "candidates": {}, "questions": {}, "artifacts": [], "log": []}
        log(st, "Session started" + (" on AUTOPILOT" if st["autopilot"] else ""))
        save(path, st)
        print(os.path.join(path, "progress.html"))
        return

    st = load(path)
    for k, v in (("mode", "full"), ("waiting", ""), ("protos", {}), ("autopilot", False), ("budget", None)):
        st.setdefault(k, v)
    for sid in STAGE_IDS:
        st["stages"].setdefault(sid, {"status": "todo"})
    if a.cmd == "status":
        print_status(st, path)
        return
    if a.cmd == "todos":
        print(json.dumps({"todos": build_todos(st)}, ensure_ascii=False, indent=2))
        return
    if a.cmd in ("stage", "now"):
        st["waiting"] = ""
    if a.cmd == "autopilot":
        st["autopilot"] = a.value == "yes"
        if a.budget is not None:
            st["budget"] = a.budget
        log(st, f"Autopilot {'ON' if st['autopilot'] else 'OFF'}"
                + (f", prototype budget ${st['budget']:g}" if st.get("budget") is not None else ""))
    elif a.cmd == "wait":
        if st.get("autopilot"):
            # Nobody is there to answer: record it, and remind the agent what to do.
            log(st, "AUTOPILOT checkpoint: " + a.text)
            print(json.dumps({"autopilot": True, "checkpoint": a.text,
                              "do": "Take the recommended option and record it with "
                                    "`log \"AUTOPILOT: <decision> — <why>\"`. Hard stops still apply."},
                             ensure_ascii=False))
        else:
            st["waiting"] = a.text; log(st, "Waiting for user: " + a.text)
    elif a.cmd == "proto":
        if a.action == "add":
            st["protos"][a.id] = {"status": "planned", "result": ""}
            log(st, f"Prototype planned for {a.id}")
        else:
            pr = st["protos"].setdefault(a.id, {"status": "planned", "result": ""})
            if a.rest:
                if a.rest[0] not in ("planned", "running", "done", "failed"):
                    sys.exit("status must be planned|running|done|failed")
                pr["status"] = a.rest[0]
            if a.result is not None: pr["result"] = a.result
            log(st, f"Prototype {a.id}: {pr['status']}" + (f" — {pr['result']}" if pr["result"] else ""))
    elif a.cmd == "stage":
        s = st["stages"][a.id]
        s["status"] = {"start": "active", "done": "done", "skip": "skip"}[a.action]
        s["t"] = now()
        if a.note is not None:
            s["note"] = a.note
        name = dict(STAGES)[a.id]
        log(st, f"{'Started' if a.action == 'start' else a.action.capitalize() + ':'} {name}"
                + (f" — {a.note}" if a.note else ""))
        if a.action == "start":
            st["now"] = name
    elif a.cmd == "now":
        st["now"] = a.text; log(st, a.text)
    elif a.cmd == "next":
        st["next"] = a.items
    elif a.cmd == "cand":
        if a.action == "add":
            name = a.rest[0] if a.rest else a.id
            idea = a.rest[1] if len(a.rest) > 1 else ""
            st["candidates"][a.id] = {"name": name, "idea": idea, "status": "proposed"}
            if a.wave:
                st["candidates"][a.id]["wave"] = a.wave
            if a.score is not None:
                st["candidates"][a.id]["score"] = parse_score(a.score)
            log(st, f"Candidate {a.id}: {name}" + (f" (wave {a.wave})" if a.wave else ""))
        else:
            c = st["candidates"].setdefault(a.id, {"name": a.id, "status": "proposed"})
            if a.rest:
                if a.rest[0] not in ("proposed", "screened_out", "finalist", "selected"):
                    sys.exit("status must be proposed|screened_out|finalist|selected")
                c["status"] = a.rest[0]
            if a.score is not None: c["score"] = parse_score(a.score)
            if a.reason is not None: c["reason"] = a.reason
            if a.wave is not None: c["wave"] = a.wave
            log(st, f"{a.id} → {STATUS_LABEL[c['status']]}" + (f" ({c['score']:g})" if a.score is not None else ""))
    elif a.cmd == "q":
        if a.action == "add":
            st["questions"][a.id] = {"text": a.rest[0] if a.rest else "", "status": "open", "cand": a.cand or ""}
            if a.voi is not None:
                st["questions"][a.id]["voi"] = parse_voi(a.voi)
        else:
            q = st["questions"][a.id]
            if a.rest:
                if a.rest[0] not in ("open", "searching", "answered", "not_found"):
                    sys.exit("status must be open|searching|answered|not_found")
                q["status"] = a.rest[0]
            if a.answer is not None: q["answer"] = a.answer
            if a.confidence is not None: q["confidence"] = a.confidence
            if a.voi is not None: q["voi"] = parse_voi(a.voi)
            log(st, f"Research {a.id}: {STATUS_LABEL[q['status']]}")
    elif a.cmd == "artifact":
        ap_ = os.path.abspath(a.path)
        st["artifacts"] = [x for x in st["artifacts"] if x["path"] != ap_]
        st["artifacts"].append({"path": ap_, "label": a.label or os.path.basename(a.path)})
        log(st, f"Saved {a.label or os.path.basename(a.path)}")
    elif a.cmd == "impact":
        rec = {"stage": a.id, "t": now(), "leader_before": a.leader_before, "leader_after": a.leader_after,
               "leader_changed": bool(a.leader_before and a.leader_after and a.leader_before != a.leader_after),
               "added": [x.strip() for x in a.added.split(",") if x.strip()],
               "removed": [x.strip() for x in a.removed.split(",") if x.strip()],
               "reached_final": a.final, "minutes": a.minutes, "note": a.note}
        st.setdefault("impact", []).append(rec)
        log(st, f"Impact of {a.id}: " + ("leader " + a.leader_before + " → " + a.leader_after + "; "
                                          if rec["leader_changed"] else "")
                + (a.note or "recorded"))
        write_impact_md(path, st)
    elif a.cmd == "log":
        log(st, a.msg)
        if a.msg.startswith("AUTOPILOT:"):
            p = os.path.join(path, "AUTOPILOT-DECISIONS.md")
            new = not os.path.exists(p)
            with open(p, "a", encoding="utf-8") as f:
                if new:
                    f.write("# Decisions taken on autopilot — please review\n\n"
                            "| Time | Stage | Decision and reason |\n|---|---|---|\n")
                stage = next((n for sid, n in STAGES if st["stages"][sid]["status"] == "active"), "—")
                text = a.msg[len("AUTOPILOT:"):].strip().replace("|", "/")
                f.write(f"| {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} | {stage} | {text} |\n")
    save(path, st)


if __name__ == "__main__":
    main()
