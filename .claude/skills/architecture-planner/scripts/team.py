#!/usr/bin/env python3
"""Team mode for architecture-planner: several people (each with their own
Claude session) run one planning job in parallel inside one git repository.

Design rules that make this safe without a server:
  * Shared state lives in <repo>/.arch/<run>/, which is a separate git worktree
    of an orphan branch `arch/<run>` (no project history). Sync commits, rebases
    and pushes ONLY there. The user's current branch, index and working tree are
    never committed to, rebased, stashed or pushed. `.arch/` is added to
    .git/info/exclude (not to the user's .gitignore).
  * Every file has exactly ONE writer, so rebases never conflict:
      run.json, stages/*.json, plan/*.json, shared stage files -> lead only
      members/<name>.json                                       -> that member
      tasks/<stage>/<task>.<member>.json / .md                  -> that member
  * Task status is derived by reading all members' files; the first member
    to finish a task wins if two ever worked on it.
  * Local views (board.html, BOARD.md) go to .local/, ignored by the arch branch.

Stdlib only. Commands print JSON so the agent can act on them.
Every command accepts --remote NAME (else: git config arch.remote, the only
remote, the current branch's upstream remote, or `origin`).

  team.py init  --run R --member NAME --input DIR --express yes|no --team N [--task "title"]
                [--autopilot yes|no] [--budget USD]
  team.py join  --run R --member NAME
  team.py stage --run R --member NAME <stage> start|done|skip [--note ".."]   (lead only)
  team.py plan  --run R --member NAME <stage> --tasks-file plan.json       (lead only)
        (--tasks '<json>' also works in bash, but quoting breaks in PowerShell)
        each task: {"id": "S1", "title": "...", "instructions": "...", "merge": false}
        non-merge tasks are dealt round-robin over joined members;
        merge tasks go to the lead and unlock when all other tasks are done
  team.py next  --run R --member NAME [--wait SECONDS]   (default 90, capped at 540;
        call Bash with timeout >= (wait + 30) s; a `wait` answer carries retry_after)
  team.py start --run R --member NAME <stage> <task>
  team.py done  --run R --member NAME <stage> <task> [--note ".."] [--score N]
  team.py status --run R
  team.py todos  --run R --member NAME
  team.py board  --run R
  team.py sync   --run R --member NAME

Set ARCH_NO_GIT=1 when several sessions share one folder on one machine
without git syncing (files are single-writer, so this is safe).
"""
import argparse
import datetime as dt
import glob
import html
import json
import os
import subprocess
import sys
import time

STAGES = [
    ("read", "Read the input folder", "lead"),
    ("probe", "Probe live data and sources", "lead"),
    ("grill", "Clarify task conditions (grill)", "lead"),
    ("brief", "Write the task brief", "lead"),
    ("scenarios", "Write test scenarios", "parallel"),
    ("wavea", "Generate wave A (blind to prior art)", "parallel"),
    ("priorart", "Find prior art and ready-made parts", "parallel"),
    ("generate", "Generate wave B + diversity check", "parallel"),
    ("craft", "Craft and safety-gates pass", "parallel"),
    ("screen", "Critic screening + review", "lead"),
    ("research", "Targeted research", "parallel"),
    ("critique", "Critic panel: deep review", "parallel"),
    ("hybrid", "Build and review a hybrid", "lead"),
    ("prototype", "Prototype the top options", "parallel"),
    ("select", "Select the final options", "lead"),
    ("report", "Write the final report", "parallel"),
    ("decide", "Record the decision (ADR) and build plan", "lead"),
]
IDS = [s[0] for s in STAGES]
NAME = {s[0]: s[1] for s in STAGES}
KIND = {s[0]: s[2] for s in STAGES}


def ts():
    return dt.datetime.now().isoformat(timespec="seconds")


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _run(args, cwd=None, inp=None):
    try:
        return subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace",
                              cwd=cwd, input=inp)
    except FileNotFoundError:  # git not installed
        return subprocess.CompletedProcess(args, 127, "", "git not found")


def repo_root():
    """Top level of the user's working tree. If called from inside an arch
    worktree (.arch/<run>), step out to the main working tree."""
    r = _run(["git", "rev-parse", "--show-toplevel"])
    if r.returncode != 0:
        return os.getcwd()
    top = os.path.normpath(r.stdout.strip())
    parent = os.path.dirname(top)
    if os.path.basename(parent) == ".arch":
        return os.path.dirname(parent)
    return top


def rd(run):
    return os.path.join(repo_root(), ".arch", run)


def jload(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ------------------------------------------------------------------ state

def run_cfg(d):
    cfg = jload(os.path.join(d, "run.json"))
    if not cfg:
        sys.exit(f"No run at {d}. Pull the repo, or ask the lead for the run id.")
    return cfg


def members(d):
    ms = [jload(p) for p in glob.glob(os.path.join(d, "members", "*.json"))]
    ms = [m for m in ms if m]
    ms.sort(key=lambda m: (m.get("role") != "lead", m.get("joined", "")))
    return ms


def plan(d, stage):
    return jload(os.path.join(d, "plan", f"{stage}.json"))


def task_records(d, stage, task_id):
    recs = [jload(p) for p in glob.glob(os.path.join(d, "tasks", stage, f"{task_id}.*.json"))]
    return [r for r in recs if r]


def task_state(d, stage, task):
    recs = task_records(d, stage, task["id"])
    done = sorted((r for r in recs if r["status"] == "done"), key=lambda r: r.get("done", ""))
    if done:
        return "done", done[0]
    started = sorted((r for r in recs if r["status"] == "started"), key=lambda r: r.get("started", ""))
    if started:
        return "started", started[0]
    return "todo", None


def stage_state(d, stage):
    s = jload(os.path.join(d, "stages", f"{stage}.json"), {})
    if s.get("status") in ("done", "skip"):
        return s["status"]
    p = plan(d, stage)
    if KIND[stage] == "parallel" and p:
        states = [task_state(d, stage, t)[0] for t in p["tasks"]]
        if states and all(x == "done" for x in states):
            return "done"
        return "active"
    return s.get("status", "todo")


def current_stage(d):
    for sid in IDS:
        if stage_state(d, sid) not in ("done", "skip"):
            return sid
    return None


def require_lead(d, member):
    cfg = run_cfg(d)
    if member != cfg["lead"]:
        sys.exit(f"Only the lead ({cfg['lead']}) can do this.")
    return cfg


# ------------------------------------------------------------------ git sync
#
# Why a worktree (and not a separate clone in .arch/.sync/):
#   * it shares the object store and the remote configuration of the user's
#     repo, so authentication and remote names just work;
#   * refs are shared between all worktrees of a repo, so several sessions on
#     one machine (each in its own `git worktree` of the project) can exchange
#     state through the local ref refs/heads/arch/<run> even with no remote.
# The arch worktree is checked out DETACHED (git forbids the same branch in two
# worktrees); the branch ref is moved explicitly with push / update-ref.

EMPTY = "0" * 40
REMOTE_OVERRIDE = None  # set from --remote


def git(*args, cwd=None):
    return _run(["git", *args], cwd=cwd or repo_root())


def git_ok():
    if os.environ.get("ARCH_NO_GIT"):
        return False
    return git("rev-parse", "--is-inside-work-tree").returncode == 0


def branch(run):
    return f"arch/{run}"


def pick_remote():
    """--remote > env ARCH_REMOTE > git config arch.remote > the only remote >
    upstream remote of the current branch > origin. None if the repo has no remote."""
    remotes = [x for x in git("remote").stdout.split() if x]
    for cand in (REMOTE_OVERRIDE, os.environ.get("ARCH_REMOTE"), git("config", "arch.remote").stdout.strip()):
        if cand:
            if cand not in remotes:
                sys.exit(f"Remote '{cand}' does not exist. Remotes: {', '.join(remotes) or 'none'}")
            return cand
    if not remotes:
        return None
    if len(remotes) == 1:
        return remotes[0]
    up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
    if up and up.split("/")[0] in remotes:
        return up.split("/")[0]
    if "origin" in remotes:
        return "origin"
    sys.exit(f"Several remotes ({', '.join(remotes)}); pass --remote NAME.")


def exclude_arch_dir():
    """Hide .arch/ from the user's `git status` via .git/info/exclude."""
    p = git("rev-parse", "--git-path", "info/exclude").stdout.strip()
    if not p:
        return
    if not os.path.isabs(p):
        p = os.path.join(repo_root(), p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    if "/.arch/" not in lines:
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if lines and lines[-1] != "" else "") + "/.arch/\n")


def ref_sha(ref):
    r = git("rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return r.stdout.strip() if r.returncode == 0 else None


def remote_tracking(remote, run):
    return f"refs/remotes/{remote}/{branch(run)}"


def fetch_arch(remote, run):
    git("fetch", "--quiet", remote, f"+refs/heads/{branch(run)}:{remote_tracking(remote, run)}")
    return ref_sha(remote_tracking(remote, run))


def ensure_worktree(d, run, create=False):
    """Make sure d is a worktree of the arch branch. Returns an error string or None."""
    if os.path.exists(os.path.join(d, ".git")):
        return None
    exclude_arch_dir()
    remote = pick_remote()
    start = None
    if remote:
        start = fetch_arch(remote, run)
    start = start or ref_sha(f"refs/heads/{branch(run)}")
    if not start:
        if not create:
            return (f"Branch {branch(run)} not found locally or on the remote. "
                    "Ask the lead to run init first, then pull.")
        tree = _run(["git", "mktree"], cwd=repo_root(), inp="").stdout.strip()
        r = git(*ident(), "commit-tree", tree, "-m", f"arch({run}): new run")
        if r.returncode != 0:
            return "could not create the arch branch: " + r.stderr.strip()
        start = r.stdout.strip()
        git("update-ref", f"refs/heads/{branch(run)}", start, EMPTY)
    if os.path.isdir(d) and os.listdir(d):
        # leftover plain folder (e.g. from ARCH_NO_GIT or an older version): move it aside
        os.replace(d, d + ".old-" + dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    os.makedirs(os.path.dirname(d), exist_ok=True)
    r = git("worktree", "add", "--detach", d, start)
    if r.returncode != 0:
        return "git worktree add failed: " + r.stderr.strip()
    return None


def ident():
    """Fallback identity so commits in the arch worktree never fail."""
    if git("config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=architecture-planner", "-c", "user.email=architecture-planner@localhost"]


def sync(d, member, msg=None):
    """Commit this run's files in the arch worktree, rebase on the shared arch
    branch, publish. Touches nothing outside .arch/<run>. Safe to call often."""
    if os.environ.get("ARCH_NO_GIT"):
        return {"sync": "skipped", "why": "ARCH_NO_GIT set (shared folder mode)"}
    if not git_ok():
        return {"sync": "skipped", "why": "not a git repo (shared folder mode)"}
    run = os.path.basename(d)
    err = ensure_worktree(d, run)
    if err:
        return {"sync": "error", "why": err}
    w = lambda *a: git(*a, cwd=d)
    w("add", "-A", ".")
    if w("diff", "--cached", "--quiet").returncode != 0:
        r = w(*ident(), "commit", "--quiet", "-m", msg or f"arch({run}): {member} update")
        if r.returncode != 0:
            return {"sync": "error", "why": "commit failed: " + (r.stderr or r.stdout).strip()[-300:]}
    remote = pick_remote()
    local_ref = f"refs/heads/{branch(run)}"
    for attempt in range(6):
        base = fetch_arch(remote, run) if remote else ref_sha(local_ref)
        head = w("rev-parse", "HEAD").stdout.strip()
        if base and w("merge-base", "--is-ancestor", base, "HEAD").returncode != 0:
            r = w(*ident(), "rebase", "--quiet", base)
            if r.returncode != 0:
                w("rebase", "--abort")
                return {"sync": "error", "why": "rebase failed: " + r.stderr.strip()[-300:]}
            head = w("rev-parse", "HEAD").stdout.strip()
        if remote:
            p = w("push", "--quiet", remote, f"HEAD:{local_ref}")
            if p.returncode == 0:
                git("update-ref", local_ref, head)
                git("update-ref", remote_tracking(remote, run), head)
                return {"sync": "ok", "remote": remote, "branch": branch(run)}
        else:
            old = ref_sha(local_ref) or EMPTY
            if base and old != base:
                time.sleep(0.2)
                continue
            if git("update-ref", local_ref, head, old).returncode == 0:
                return {"sync": "local", "branch": branch(run),
                        "why": "no remote; sessions on this machine share the branch ref"}
        time.sleep(0.5 + attempt)
    return {"sync": "error", "why": "publish kept failing; run sync again"}


# ------------------------------------------------------------------ commands

def in_git_repo():
    return _run(["git", "rev-parse", "--is-inside-work-tree"]).returncode == 0


def cmd_init(a):
    d = rd(a.run)
    if in_git_repo():
        exclude_arch_dir()  # keep the user's `git status` clean in every mode
    if git_ok():
        if ref_sha(f"refs/heads/{branch(a.run)}") or (pick_remote() and fetch_arch(pick_remote(), a.run)):
            sys.exit(f"Run {a.run} already exists (branch {branch(a.run)}). Use join, or pick another --run.")
        err = ensure_worktree(d, a.run, create=True)
        if err:
            sys.exit(err)
        if a.remote:
            git("config", "arch.remote", a.remote)
    elif os.path.exists(os.path.join(d, "run.json")):
        sys.exit(f"Run {a.run} already exists. Use join, or pick another --run.")
    cfg = {"run": a.run, "task": a.task or a.run, "input": a.input, "express": a.express == "yes",
           "team_size": a.team, "lead": a.member, "created": ts(),
           "autopilot": a.autopilot == "yes", "budget": a.budget}
    jsave(os.path.join(d, "run.json"), cfg)
    jsave(os.path.join(d, "members", f"{a.member}.json"), {"name": a.member, "role": "lead", "joined": ts()})
    with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8", newline="\n") as f:
        f.write(".local/\n*.tmp\n")
    with open(os.path.join(d, ".gitattributes"), "w", encoding="utf-8", newline="\n") as f:
        f.write("* text=auto eol=lf\n")
    r = sync(d, a.member, f"arch({a.run}): start run")
    out({"ok": True, "run_dir": d, "branch": branch(a.run), "sync": r,
         "tell_teammates": f"/architecture-planner --run {a.run} --member <your-name>"})


def cmd_join(a):
    d = rd(a.run)
    if in_git_repo():
        exclude_arch_dir()
    if git_ok():
        if a.remote:
            git("config", "arch.remote", a.remote)
        err = ensure_worktree(d, a.run)
        if err:
            sys.exit(err)
        sync(d, a.member)
    cfg = run_cfg(d)
    p = os.path.join(d, "members", f"{a.member}.json")
    if not os.path.exists(p):
        jsave(p, {"name": a.member, "role": "lead" if a.member == cfg["lead"] else "worker", "joined": ts()})
    ms = members(d)
    r = sync(d, a.member, f"arch({a.run}): {a.member} joined")
    out({"ok": True, "config": cfg, "members": [m["name"] for m in ms], "sync": r,
         "warning": (f"{len(ms)} members joined but team size is {cfg['team_size']}"
                     if len(ms) > cfg["team_size"] else None)})


def cmd_stage(a):
    d = rd(a.run)
    require_lead(d, a.member)
    p = os.path.join(d, "stages", f"{a.stage}.json")
    s = jload(p, {})
    s.update({"status": {"start": "active", "done": "done", "skip": "skip"}[a.action], "t": ts()})
    if a.note is not None:
        s["note"] = a.note
    jsave(p, s)
    out({"ok": True, "stage": a.stage, "status": s["status"], "sync": sync(d, a.member)})


def cmd_plan(a):
    d = rd(a.run)
    cfg = require_lead(d, a.member)
    if KIND[a.stage] != "parallel":
        sys.exit(f"{a.stage} is a lead-only stage; it has no task plan.")
    if a.tasks_file:
        with open(a.tasks_file, encoding="utf-8") as f:
            tasks = json.load(f)
    elif a.tasks:
        tasks = json.loads(a.tasks)
    else:
        sys.exit("Give --tasks-file <path> (recommended, works in every shell) or --tasks '<json>'.")
    names = [m["name"] for m in members(d)]
    i = 0
    for t in tasks:
        if t.get("merge"):
            t["assignee"] = cfg["lead"]
        elif not t.get("assignee"):
            t["assignee"] = names[i % len(names)]
            i += 1
    jsave(os.path.join(d, "plan", f"{a.stage}.json"), {"stage": a.stage, "created": ts(), "tasks": tasks})
    out({"ok": True, "stage": a.stage,
         "assignment": {n: [t["id"] for t in tasks if t["assignee"] == n] for n in names},
         "sync": sync(d, a.member, f"arch({a.run}): plan {a.stage}")})


def pick(d, member):
    cfg = run_cfg(d)
    st = current_stage(d)
    if st is None:
        return {"action": "finished", "message": "All stages done. The lead has the final report."}
    if KIND[st] == "lead" or not plan(d, st):
        if member == cfg["lead"]:
            hint = ("Create the task plan for this stage with `plan`, then call next again."
                    if KIND[st] == "parallel" else "This stage is yours: do it, then mark it done.")
            return {"action": "lead_stage", "stage": st, "stage_name": NAME[st], "hint": hint}
        return {"action": "wait", "stage": st, "stage_name": NAME[st],
                "message": f"Waiting for the lead ({cfg['lead']}) to finish: {NAME[st]}"}
    tasks = plan(d, st)["tasks"]
    regular = [t for t in tasks if not t.get("merge")]
    regular_done = all(task_state(d, st, t)[0] == "done" for t in regular)
    avail = [t for t in tasks if task_state(d, st, t)[0] != "done" and (not t.get("merge") or regular_done)]

    def mine_started(t):
        s, rec = task_state(d, st, t)
        return s == "started" and rec["member"] == member

    for t in avail:  # resume something I already started
        if mine_started(t):
            return {"action": "task", "stage": st, "task": t, "resumed": True}
    for t in avail:  # my own unstarted tasks
        if t["assignee"] == member and task_state(d, st, t)[0] == "todo":
            return {"action": "task", "stage": st, "task": t}
    for t in avail:  # steal unstarted work from others (never merge tasks)
        if not t.get("merge") and task_state(d, st, t)[0] == "todo":
            return {"action": "task", "stage": st, "task": t, "stolen_from": t["assignee"]}
    waiting_on = sorted({(task_state(d, st, t)[1] or {}).get("member", t["assignee"])
                         for t in tasks if task_state(d, st, t)[0] != "done"})
    return {"action": "wait", "stage": st, "stage_name": NAME[st],
            "message": f"My tasks in '{NAME[st]}' are done; waiting for: {', '.join(waiting_on)}"}


MAX_WAIT = 540  # Claude Code kills a Bash call after at most 600 s


def cmd_next(a):
    d = rd(a.run)
    requested = a.wait if a.wait is not None else 90
    wait = max(0, min(requested, MAX_WAIT))
    deadline = time.monotonic() + wait
    while True:
        sync(d, a.member)
        r = pick(d, a.member)
        left = deadline - time.monotonic()
        if r["action"] != "wait" or left <= 0:
            if r["action"] == "wait":
                r["retry_after"] = 5
                r["hint"] = f"Call next again (Bash timeout >= {wait + 30} s)."
            if requested != wait:
                r["wait_capped"] = {"requested": requested, "used": wait,
                                    "why": f"maximum is {MAX_WAIT} s so the Bash call is not killed"}
            out(r)
            return
        time.sleep(min(15, left))


def write_task(d, stage, task_id, member, **kw):
    p = os.path.join(d, "tasks", stage, f"{task_id}.{member}.json")
    rec = jload(p, {"task": task_id, "stage": stage, "member": member})
    rec.update(kw)
    jsave(p, rec)
    return rec


def cmd_start(a):
    d = rd(a.run)
    rec = write_task(d, a.stage, a.task, a.member, status="started", started=ts())
    out({"ok": True, "record": rec,
         "write_output_to": os.path.join(d, "tasks", a.stage, f"{a.task}.{a.member}.md"),
         "sync": sync(d, a.member)})


def cmd_done(a):
    d = rd(a.run)
    md = os.path.join(d, "tasks", a.stage, f"{a.task}.{a.member}.md")
    if not os.path.exists(md):
        sys.exit(f"Write the task output to {md} before marking it done.")
    kw = {"status": "done", "done": ts()}
    if a.note is not None: kw["note"] = a.note
    if a.score is not None: kw["score"] = a.score
    rec = write_task(d, a.stage, a.task, a.member, **kw)
    out({"ok": True, "record": rec, "sync": sync(d, a.member)})


def summary(d):
    cfg = run_cfg(d)
    stages = []
    for sid in IDS:
        s = jload(os.path.join(d, "stages", f"{sid}.json"), {})
        item = {"id": sid, "name": NAME[sid], "kind": KIND[sid], "status": stage_state(d, sid), "note": s.get("note")}
        p = plan(d, sid)
        if p:
            item["tasks"] = []
            for t in p["tasks"]:
                state, rec = task_state(d, sid, t)
                item["tasks"].append({"id": t["id"], "title": t.get("title", ""), "assignee": t["assignee"],
                                      "merge": bool(t.get("merge")), "state": state,
                                      "by": (rec or {}).get("member"), "note": (rec or {}).get("note"),
                                      "score": (rec or {}).get("score")})
        stages.append(item)
    return {"config": cfg, "members": [m["name"] for m in members(d)],
            "current_stage": current_stage(d), "stages": stages}


def cmd_status(a):
    out(summary(rd(a.run)))


def cmd_todos(a):
    s = summary(rd(a.run))
    todos, active_used = [], False
    for i, st in enumerate(s["stages"]):
        status = {"done": "completed", "skip": "completed"}.get(st["status"], "pending")
        if st["id"] == s["current_stage"]:
            status = "in_progress"; active_used = True
        label = f"{i}. {st['name']}" + (f" ({st['note']})" if st.get("note") and status == "completed" else "")
        if st["id"] == s["current_stage"] and st["kind"] == "lead" and a.member != s["config"]["lead"]:
            label += f" — lead: {s['config']['lead']}"
        todos.append({"content": label, "status": status, "activeForm": st["name"]})
        if st["id"] == s["current_stage"] and st.get("tasks"):
            for t in st["tasks"]:
                who = t["by"] or t["assignee"]
                me = " (me)" if who == a.member else f" ({who})"
                todos.append({"content": f"   {t['id']}: {t['title']}{me}",
                              "status": "completed" if t["state"] == "done" else "pending",
                              "activeForm": f"{t['id']}"})
    out({"todos": todos})


def cmd_board(a):
    d = rd(a.run)
    s = summary(d)
    cfg = s["config"]
    local = os.path.join(d, ".local")
    os.makedirs(local, exist_ok=True)
    e = lambda x: html.escape(str(x or ""))
    done_n = sum(1 for st in s["stages"] if st["status"] in ("done", "skip"))
    pct = int(done_n / len(IDS) * 100)

    rows = []
    for i, st in enumerate(s["stages"]):
        cls = {"done": "done", "skip": "done", "active": "active"}.get(st["status"], "todo")
        if st["id"] == s["current_stage"]:
            cls = "active"
        who = "everyone" if st["kind"] == "parallel" else cfg["lead"]
        tasks = ""
        if st.get("tasks"):
            cells = "".join(
                f'<span class="t {t["state"]}" title="{e(t["title"])}">{e(t["id"])} · {e(t["by"] or t["assignee"])}'
                f'{" ★" if t["merge"] else ""}</span>' for t in st["tasks"])
            tasks = f'<div class="tasks">{cells}</div>'
        note = f'<div class="note">{e(st["note"])}</div>' if st.get("note") else ""
        rows.append(f'<li class="{cls}"><span class="dot">{"✓" if cls == "done" else i}</span>'
                    f'<b>{e(st["name"])}</b> <span class="who">{e(who)}</span>'
                    f'{note}{tasks}</li>')

    load = {}
    for st in s["stages"]:
        for t in st.get("tasks") or []:
            w = t["by"] or t["assignee"]
            load.setdefault(w, [0, 0])
            load[w][0] += t["state"] == "done"; load[w][1] += 1
    people = "".join(f'<tr><td>{e(m)}{" (lead)" if m == cfg["lead"] else ""}</td><td>{load.get(m, [0,0])[0]} / {load.get(m, [0,0])[1]}</td></tr>'
                     for m in s["members"])
    refresh = '<meta http-equiv="refresh" content="10">' if s["current_stage"] else ""
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>{e(cfg['task'])} · team run</title><style>
body{{margin:0;background:#EEF1F4;color:#1C2733;font:15px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:26px 20px 60px}}
h1{{font:600 25px/1.2 "IBM Plex Serif",Georgia,serif;margin:0}} h2{{font:600 16px "IBM Plex Serif",Georgia,serif}}
header{{border-bottom:2px solid #1C2733;padding-bottom:12px;margin-bottom:20px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;align-items:end}}
.sub,.who,.note{{color:#6B7785;font-size:13px}} .bar{{height:6px;width:220px;background:#C9D1DA;border-radius:3px}} .bar i{{display:block;height:100%;background:#2F5D8A;border-radius:3px}}
.grid{{display:grid;grid-template-columns:1fr 260px;gap:28px}} @media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
ol{{list-style:none;padding:0;margin:0}} li{{position:relative;padding:0 0 16px 34px}}
.dot{{position:absolute;left:0;top:1px;width:22px;height:22px;border-radius:50%;border:2px solid #C9D1DA;font-size:11px;line-height:18px;text-align:center;color:#6B7785;background:#EEF1F4}}
li.done .dot{{background:#2E7D6B;border-color:#2E7D6B;color:#fff}} li.active .dot{{border-color:#B7791F;color:#B7791F;box-shadow:0 0 0 4px rgba(183,121,31,.18)}}
li.todo b{{color:#6B7785;font-weight:400}}
.tasks{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}} .t{{font-size:12px;padding:2px 8px;border-radius:10px;border:1px solid #C9D1DA;background:#F8FAFB}}
.t.done{{background:#2E7D6B;border-color:#2E7D6B;color:#fff}} .t.started{{border-color:#B7791F;color:#B7791F}}
table{{width:100%;border-collapse:collapse;background:#F8FAFB}} td{{padding:6px 10px;border-bottom:1px solid #C9D1DA}}
</style></head><body><main><header><div><h1>{e(cfg['task'])}</h1>
<div class="sub">Team run {e(cfg['run'])} · {len(s['members'])} of {cfg['team_size']} joined · {'express' if cfg['express'] else 'full'} mode · lead {e(cfg['lead'])}</div></div>
<div><div class="sub">{done_n} of {len(IDS)} stages · {pct}%</div><div class="bar"><i style="width:{pct}%"></i></div></div></header>
<div class="grid"><section><h2>Pipeline</h2><ol>{''.join(rows)}</ol></section>
<section><h2>People</h2><table><tr><td class="sub">Member</td><td class="sub">Tasks done</td></tr>{people}</table>
<p class="sub">Orange = in progress, green = done, ★ = merge task (lead). Run <code>team.py next</code> to pick up work.</p></section></div>
</main></body></html>"""
    with open(os.path.join(local, "board.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    md = [f"# {cfg['task']} — team run {cfg['run']} ({pct}%)", ""]
    for i, st in enumerate(s["stages"]):
        icon = "✅" if st["status"] in ("done", "skip") else ("▶️" if st["id"] == s["current_stage"] else "⏳")
        md.append(f"{icon} {i}. {st['name']}" + (f" — {st['note']}" if st.get("note") else ""))
        if st["id"] == s["current_stage"] and st.get("tasks"):
            for t in st["tasks"]:
                md.append(f"    {'✅' if t['state']=='done' else '🔄' if t['state']=='started' else '·'} {t['id']} {t['title']} — {t['by'] or t['assignee']}")
    with open(os.path.join(local, "BOARD.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    out({"ok": True, "board": os.path.join(local, "board.html")})


def cmd_sync(a):
    out(sync(rd(a.run), a.member))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def base(name, member=True):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--remote", help="git remote to sync through (default: auto)")
        if member:
            p.add_argument("--member", required=True)
        return p

    p = base("init"); p.add_argument("--input", required=True); p.add_argument("--express", choices=["yes", "no"], default="no")
    p.add_argument("--team", type=int, default=1); p.add_argument("--task")
    p.add_argument("--autopilot", choices=["yes", "no"], default="no"); p.add_argument("--budget", type=float)
    base("join")
    p = base("stage"); p.add_argument("stage", choices=IDS); p.add_argument("action", choices=["start", "done", "skip"]); p.add_argument("--note")
    p = base("plan"); p.add_argument("stage", choices=IDS); p.add_argument("--tasks"); p.add_argument("--tasks-file")
    p = base("next"); p.add_argument("--wait", type=int, default=90)
    p = base("start"); p.add_argument("stage", choices=IDS); p.add_argument("task")
    p = base("done"); p.add_argument("stage", choices=IDS); p.add_argument("task"); p.add_argument("--note"); p.add_argument("--score")
    base("status", member=False)
    base("todos")
    base("board", member=False)
    base("sync")
    a = ap.parse_args()
    global REMOTE_OVERRIDE
    REMOTE_OVERRIDE = getattr(a, "remote", None)
    {"init": cmd_init, "join": cmd_join, "stage": cmd_stage, "plan": cmd_plan, "next": cmd_next,
     "start": cmd_start, "done": cmd_done, "status": cmd_status, "todos": cmd_todos,
     "board": cmd_board, "sync": cmd_sync}[a.cmd](a)


if __name__ == "__main__":
    main()
