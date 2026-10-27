#!/usr/bin/env python3
"""Probe of the #119 price sources from the current network (#219). Not part of the image.

The #119 recon ran from a Latvian IP; from a Russian IP the answers may differ (RZD drops
foreign IPs, Qrator, SmartCaptcha). Copy this one file to a machine in Russia and run:

    python3 probe_sources.py                       # all sources, report in the current dir
    python3 probe_sources.py --out /tmp/probe      # report elsewhere
    python3 probe_sources.py --only 101hotels.com,ticket.rzd.ru
    TRAVELPAYOUTS_TOKEN=... YANDEX_RASP_KEY=... python3 probe_sources.py   # APIs with keys

For every host: DNS, TCP and TLS times, then GET `/`, `/robots.txt` and one city or route
page if robots.txt allows it for our agent. Each answer gets its status, redirect, size,
time, the protection seen (Qrator, SmartCaptcha, DataDome, Cloudflare, DDoS-Guard, a «not a
robot» page) and, for the sample page, how many facts the collector would find.
The APIs are probed without a key (is the endpoint alive?) and, with a key from the
environment, with one real request; the key goes in a header and is never printed.

Politeness: the honest UA of scripts/collect_prices.py, one request at a time, PAUSE_S
between requests; about 40 requests in total. Ostrovok is not probed (#119).
Output: probe-<time>.md (a table for a comment in #119, without the IP address) and
probe-<time>.json with the details. Standard library only, Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Python 3.8+: the remote machine may be old, so no datetime.UTC (3.11) and no dict | dict.

AGENT = "nedovost-price-probe"
UA = f"Mozilla/5.0 (compatible; {AGENT}/0.1; +https://github.com/ostrovok-hackathon-2026/nedovost-org)"
PAUSE_S = 3.0
TIMEOUT_S = 20
READ_MAX = 3_000_000
# Same rule as collect_prices.py: city pages load captcha scripts too, so the word counts
# only on a small page (challenge pages in the recon were 4-25 KB, city pages 500+ KB).
CHALLENGE_MAX = 50_000
REFUSAL = ("вы не робот", "проверяем, что вы не бот", "подтвердите, что вы не робот")
REFUSAL_CODES = {401, 403, 429, 432, 498, 503}

# Sources of the #119 recon table; `lv` is what the recon saw from the Latvian IP (27.10).
SITES = [
    {
        "host": "101hotels.com",
        "page": "/main/cities/sochi",
        "facts": r'itemtype="https://schema\.org/Hotel"',
        "lv": "200, Qrator пропускает обычный curl",
    },
    {
        "host": "www.tutu.ru",
        "page": "/poezda/Moskva/Sankt-Peterburg/",
        "facts": r'"lowPrice"',
        "lv": "200, защиты нет",
    },
    {"host": "ticket.rzd.ru", "lv": "соединение сброшено за 0,4 с"},
    {"host": "pass.rzd.ru", "lv": "соединение сброшено за 0,4 с"},
    {"host": "travel.yandex.ru", "lv": "SmartCaptcha на главной"},
    {"host": "bronevik.com", "lv": "Qrator"},
    {"host": "sutochno.ru", "lv": "/search и /api запрещены robots.txt"},
    {"host": "www.tripadvisor.ru", "lv": "DataDome, 403"},
    {"host": "www.trip.com", "lv": "432"},
    {"host": "www.booking.com", "lv": "челлендж, 202"},
    {"host": "level.travel", "lv": "«Проверяем, что вы не бот»"},
    {"host": "www.aviasales.ru", "lv": "/hotels/search запрещён robots.txt"},
]


def _month(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m")  # noqa: UP017


def _day(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")  # noqa: UP017


APIS = [
    {
        "name": "Travelpayouts prices_for_dates",
        "host": "api.travelpayouts.com",
        "path": "/aviasales/v3/prices_for_dates?origin=MOW&destination=AER"
        f"&departure_at={_month(30)}&currency=rub&limit=5",
        "env": "TRAVELPAYOUTS_TOKEN",
        "header": "X-Access-Token",
        "count": "data",
        "lv": "401 без токена — endpoint жив",
    },
    {
        "name": "Яндекс.Расписания search",
        "host": "api.rasp.yandex.net",
        "path": f"/v3.0/search/?from=c213&to=c239&date={_day(30)}&transport_types=train"
        "&format=json&lang=ru_RU",
        "env": "YANDEX_RASP_KEY",
        "header": "Authorization",
        "count": "segments",
        "lv": "400 «Не указан ключ» — endpoint жив",
    },
]


# --- pure checks (tested in backend/tests/test_probe_sources.py) ---


def protection(status: int, headers: dict[str, str], body: str, url: str = "") -> list[str]:
    """Names of the protection layers seen in one answer; headers have lower-case names."""
    server = headers.get("server", "").lower()
    cookies = headers.get("set-cookie", "").lower()
    low = body.lower()
    seen = []
    if "qrator" in server or "qrator" in cookies or "qrator" in low[:CHALLENGE_MAX]:
        seen.append("Qrator")
    if "ddos-guard" in server or "__ddg" in cookies:
        seen.append("DDoS-Guard")
    if "cf-ray" in headers or "cloudflare" in server:
        challenge = "challenge-platform" in low or "cf-chl" in low or status in (403, 503)
        seen.append("Cloudflare (челлендж)" if challenge else "Cloudflare")
    if "x-datadome" in headers or "datadome" in cookies or "captcha-delivery.com" in low:
        seen.append("DataDome")
    if "smartcaptcha" in low or "showcaptcha" in url.lower() or "showcaptcha" in low:
        seen.append("SmartCaptcha")
    if "variti" in low or "variti" in cookies:
        seen.append("Variti")
    if "servicepipe" in low or "spsc" in cookies:
        seen.append("ServicePipe")
    if any(w in low for w in REFUSAL) or (len(body) < CHALLENGE_MAX and "captcha" in low):
        seen.append("страница «вы не робот»")
    return seen


def verdict(status: int | None, seen: list[str], error: str = "") -> str:
    """«ок» when the page came through; otherwise why not."""
    if error:
        return f"ошибка: {error}"
    if status is None:
        return "нет ответа"
    blocking = [s for s in seen if s not in ("Qrator", "Cloudflare", "DDoS-Guard")]
    if status in REFUSAL_CODES or status == 202 or blocking:
        return f"отказ: {status}" + (f", {', '.join(blocking)}" if blocking else "")
    if status >= 400:
        return f"HTTP {status}"
    return "ок"


def facts(body: str, pattern: str) -> int:
    return len(re.findall(pattern, body))


def robots_allows(robots_txt: str, path: str) -> bool:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())
    return rp.can_fetch(AGENT, path)


# --- network ---


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:160]


def connect(host: str) -> dict:
    """DNS, TCP and TLS steps one by one, so a reset shows at the step where it happened."""
    out: dict = {}
    t0 = time.monotonic()
    try:
        ip = str(socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)[0][4][0])
        out["dns_ms"] = _ms(t0)
    except OSError as e:
        out["dns_error"] = _err(e)
        return out
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((ip, 443), timeout=TIMEOUT_S)
        out["tcp_ms"] = _ms(t0)
    except OSError as e:
        out["tcp_error"] = _err(e)
        return out
    t0 = time.monotonic()
    try:
        with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
            out["tls_ms"] = _ms(t0)
            out["tls_version"] = tls.version()
    except (OSError, ssl.SSLError) as e:
        out["tls_error"] = _err(e)
        out["tls_fail_ms"] = _ms(t0)
        sock.close()
    return out


def get(url: str, headers: dict[str, str] | None = None) -> dict:
    """One GET; the body is kept only for the checks and is not written to the report."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            status, final, hdrs, body = r.status, r.geturl(), r.headers, r.read(READ_MAX)
    except urllib.error.HTTPError as e:
        status, final, hdrs = e.code, e.geturl(), e.headers
        try:
            body = e.read(READ_MAX)
        except OSError:
            body = b""
    except (OSError, ssl.SSLError) as e:
        return {"url": url, "error": _err(e), "ms": _ms(t0)}
    text = body.decode("utf-8", "replace")
    low_headers = {k.lower(): v for k, v in hdrs.items()} if hdrs else {}
    # robots.txt is plain text and may say «Disallow: /captcha»; a challenge comes as HTML
    plain = "html" not in low_headers.get("content-type", "html")
    seen = [] if plain else protection(status, low_headers, text, final)
    return {
        "url": url,
        "status": status,
        "final_url": final if final != url else None,
        "bytes": len(body),
        "ms": _ms(t0),
        "server": low_headers.get("server"),
        "protection": seen,
        "verdict": verdict(status, seen),
        "_text": text,
    }


def egress() -> dict:
    r = get("https://ipinfo.io/json")
    try:
        data = json.loads(r.pop("_text", "") or "{}")
    except ValueError:
        data = {}
    return {**{k: data.get(k) for k in ("ip", "country", "region", "org")}, "probe": r}


def probe_site(site: dict, pause: float) -> dict:
    host = site["host"]
    res = {"host": host, "lv": site["lv"], **connect(host)}
    time.sleep(pause)
    root = get(f"https://{host}/")
    root.pop("_text", None)
    res["root"] = root
    time.sleep(pause)
    robots = get(f"https://{host}/robots.txt")
    robots_txt = robots.pop("_text", "") if robots.get("status") == 200 else ""
    res["robots"] = robots
    page = site.get("page")
    if page:
        if robots_txt and not robots_allows(robots_txt, page):
            res["page"] = {"url": f"https://{host}{page}", "verdict": "запрещено robots.txt"}
        else:
            time.sleep(pause)
            p = get(f"https://{host}{page}")
            p["facts"] = facts(p.pop("_text", ""), site["facts"])
            if p.get("verdict") == "ок" and not p["facts"]:
                p["verdict"] = "200 без фактов (вызов или новая разметка)"
            res["page"] = p
    return res


def probe_api(api: dict) -> dict:
    url = f"https://{api['host']}{api['path']}"
    res = {"name": api["name"], "lv": api["lv"], "no_key": _api_get(url, {}, api)}
    key = os.environ.get(api["env"], "").strip()
    if key:
        time.sleep(PAUSE_S)
        res["with_key"] = _api_get(url, {api["header"]: key}, api)
    return res


def _api_get(url: str, headers: dict[str, str], api: dict) -> dict:
    r = get(url, headers)
    text = r.pop("_text", "")
    r.pop("protection", None)
    r["verdict"] = "ок" if r.get("status") == 200 else f"HTTP {r.get('status', r.get('error'))}"
    try:
        data = json.loads(text)
    except ValueError:
        r["answer"] = text[:120]
        return r
    items = data.get(api["count"]) if isinstance(data, dict) else None
    if isinstance(items, list):
        r["items"] = len(items)
    else:
        r["answer"] = json.dumps(data, ensure_ascii=False)[:120]
    return r


# --- report ---


def _short(error: str) -> str:
    """Timeout and reset differ for #119: a reset in 0.4 s is a block, a timeout may be a route."""
    low = error.lower()
    for words, name in (
        (("timed out", "timeout"), "таймаут"),
        (("reset",), "сброс соединения"),
        (("refused",), "соединение отклонено"),
        (("name or service", "nodename", "getaddrinfo"), "нет DNS"),
        (("certificate",), "ошибка сертификата"),
    ):
        if any(w in low for w in words):
            return name
    return error.split(":")[0]


def _step(res: dict, name: str) -> str:
    if f"{name}_error" in res:
        return "✗ " + _short(res[f"{name}_error"])
    return f"{res[f'{name}_ms']} мс" if f"{name}_ms" in res else "—"


def _answer(r: dict | None) -> str:
    if not r:
        return "—"
    if "error" in r:
        return f"✗ {_short(r['error'])}, {r['ms']} мс"
    out = f"{r['status']}, {r['bytes'] // 1024} КБ, {r['ms']} мс"
    if r.get("final_url"):
        out += f" → {r['final_url'][:60]}"
    return out


def render(report: dict) -> str:
    """Markdown for a comment in #119. No IP address: the issue is public to the team."""
    net = report["egress"]
    lines = [
        f"## Проба источников #119 с сети {net.get('country') or '?'} ({net.get('org') or '?'})",
        "",
        (
            f"Время: {report['started']} UTC. Скрипт: `scripts/probe_sources.py` (#219). "
            f"Пауза {report['pause']} с, запросов: {report['requests']}."
        ),
        "",
    ]
    if net.get("country") != "RU":
        lines += ["**Внимание: выходной IP не российский — проба не отвечает на вопрос #119.**", ""]
    lines += [
        "| Хост | DNS | TCP | TLS | `/` | robots.txt | Страница | Защита | Итог | На LV-IP 27.10 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["sites"]:
        page = s.get("page")
        page_cell = "—"
        if page:
            page_cell = page["verdict"] if "status" not in page else _answer(page)
            if "facts" in page:
                page_cell += f", фактов: {page['facts']}"
        seen = sorted(
            {p for r in (s.get("root"), s.get("page")) if r for p in r.get("protection", [])}
        )
        main = page if page and "status" in page else s.get("root") or {}
        if "tls_error" in s or "tcp_error" in s or "dns_error" in s:
            result = "нет соединения"
        else:
            result = main.get("verdict", "—")
        lines.append(
            f"| {s['host']} | {_step(s, 'dns')} | {_step(s, 'tcp')} | {_step(s, 'tls')} "
            f"| {_answer(s.get('root'))} | {_answer(s.get('robots'))} | {page_cell} "
            f"| {', '.join(seen) or 'нет'} | {result} | {s['lv']} |"
        )
    if report["apis"]:
        lines += ["", "| API | Без ключа | С ключом | На LV-IP 27.10 |", "|---|---|---|---|"]
    for a in report["apis"]:
        cells = []
        for k in ("no_key", "with_key"):
            r = a.get(k)
            if not r:
                cells.append("ключ не задан")
            elif "error" in r:
                cells.append(f"✗ {_short(r['error'])}")
            else:
                extra = f"записей: {r['items']}" if "items" in r else r.get("answer", "")
                cells.append(f"{r['status']}, {extra}".rstrip(", "))
        lines.append(f"| {a['name']} | {cells[0]} | {cells[1]} | {a['lv']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--out", default=".", help="каталог для probe-<время>.md и .json")
    ap.add_argument("--only", default="", help="хосты через запятую (по умолчанию все)")
    ap.add_argument("--pause", type=float, default=PAUSE_S, help="пауза между запросами, с")
    args = ap.parse_args(argv)
    only = {h.strip() for h in args.only.split(",") if h.strip()}
    started = datetime.now(timezone.utc)  # noqa: UP017
    report: dict = {"started": started.strftime("%Y-%m-%d %H:%M"), "pause": args.pause}
    print("выходной IP…", file=sys.stderr)
    report["egress"] = egress()
    if report["egress"].get("country") != "RU":
        print(f"ВНИМАНИЕ: страна {report['egress'].get('country')}, не RU", file=sys.stderr)
    sites = [s for s in SITES if not only or s["host"] in only]
    report["sites"] = []
    for s in sites:
        print(f"{s['host']}…", file=sys.stderr)
        report["sites"].append(probe_site(s, args.pause))
    report["apis"] = []
    for a in APIS:
        if only and a["host"] not in only:
            continue
        print(f"{a['host']}…", file=sys.stderr)
        report["apis"].append(probe_api(a))
        time.sleep(args.pause)
    report["requests"] = (
        1
        + sum(2 + bool(s.get("page")) for s in sites)
        + sum(1 + bool(os.environ.get(a["env"])) for a in APIS if not only or a["host"] in only)
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"probe-{started.strftime('%Y%m%d-%H%M')}"
    md = render(report)
    Path(f"{stem}.md").write_text(md, encoding="utf-8")
    Path(f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(md)
    print(f"Отчёт: {stem}.md (без IP, для #119), детали: {stem}.json (с IP)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
