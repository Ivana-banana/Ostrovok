"""Probe of the #119 price sources (#219): pure checks of scripts/probe_sources.py, no network.

Headers and bodies copy the signatures seen in the #119 recon (27.10) with made-up values.
"""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "scripts" / "probe_sources.py"
_spec = importlib.util.spec_from_file_location("probe_sources", _path)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

BIG_PAGE = "<html>" + "x" * 60_000 + '<script src="https://smartcaptcha.yandexcloud.net/x.js">'


def test_plain_page_has_no_protection_and_passes():
    seen = ps.protection(200, {"server": "nginx"}, "<html>ok</html>")
    assert seen == []
    assert ps.verdict(200, seen) == "ок"


def test_qrator_that_lets_the_page_through_is_not_a_refusal():
    seen = ps.protection(200, {"server": "QRATOR"}, "<html>" + "x" * 60_000)
    assert seen == ["Qrator"]
    assert ps.verdict(200, seen) == "ок"


def test_captcha_script_on_a_big_page_is_not_a_refusal():
    # 101hotels city pages load SmartCaptcha scripts; collect_prices.py uses the same rule
    seen = ps.protection(200, {}, BIG_PAGE.replace("smartcaptcha", "captcha"))
    assert seen == []


def test_small_captcha_page_is_a_refusal():
    seen = ps.protection(200, {}, "<html>Подтвердите, что вы не робот</html>")
    assert "страница «вы не робот»" in seen
    assert ps.verdict(200, seen).startswith("отказ")


def test_smartcaptcha_redirect_is_a_refusal():
    seen = ps.protection(200, {}, "<html></html>", "https://travel.yandex.ru/showcaptcha?retpath=x")
    assert "SmartCaptcha" in seen
    assert ps.verdict(200, seen).startswith("отказ")


def test_datadome_403_is_a_refusal():
    seen = ps.protection(403, {"x-datadome": "protected"}, "<html></html>")
    assert "DataDome" in seen
    assert ps.verdict(403, seen) == "отказ: 403, DataDome"


def test_cloudflare_challenge_is_named():
    seen = ps.protection(403, {"cf-ray": "1-ARN", "server": "cloudflare"}, "cf-chl")
    assert seen == ["Cloudflare (челлендж)"]
    assert ps.verdict(403, seen).startswith("отказ: 403")


def test_trip_com_432_and_booking_202_are_refusals():
    assert ps.verdict(432, []).startswith("отказ: 432")
    assert ps.verdict(202, []).startswith("отказ: 202")


def test_connection_error_is_reported_as_an_error():
    assert (
        ps.verdict(None, [], "ConnectionResetError: reset") == "ошибка: ConnectionResetError: reset"
    )


def test_facts_are_counted_by_the_site_pattern():
    hotel = ps.SITES[0]
    card = '<li itemscope itemtype="https://schema.org/Hotel">'
    assert ps.facts(card * 3, hotel["facts"]) == 3
    assert ps.facts('{"lowPrice": 801}', ps.SITES[1]["facts"]) == 1


def test_robots_txt_is_respected_for_the_sample_page():
    robots = "User-agent: *\nDisallow: /search\n"
    assert ps.robots_allows(robots, "/main/cities/sochi")
    assert not ps.robots_allows(robots, "/search?city=sochi")


def test_ostrovok_is_not_probed():
    hosts = [s["host"] for s in ps.SITES] + [a["host"] for a in ps.APIS]
    assert not any("ostrovok" in h for h in hosts)


def test_report_has_no_ip_and_no_key():
    report = {
        "started": "2026-10-27 08:00",
        "pause": 3.0,
        "requests": 4,
        "egress": {"ip": "203.0.113.7", "country": "RU", "org": "AS0 Example"},
        "sites": [
            {
                "host": "ticket.rzd.ru",
                "lv": "соединение сброшено за 0,4 с",
                "dns_ms": 5,
                "tcp_ms": 20,
                "tls_error": "ConnectionResetError: [Errno 104] reset",
            }
        ],
        "apis": [
            {
                "name": "Яндекс.Расписания search",
                "lv": "400",
                "no_key": {"status": 400, "answer": "Не указан ключ"},
                "with_key": {"status": 200, "items": 12},
            }
        ],
    }
    md = ps.render(report)
    assert "203.0.113.7" not in md
    assert "нет соединения" in md
    assert "записей: 12" in md
    assert "Внимание" not in md  # RU egress: no warning


def test_report_warns_when_the_egress_is_not_russian():
    report = {
        "started": "x",
        "pause": 3.0,
        "requests": 1,
        "egress": {"country": "EE"},
        "sites": [],
        "apis": [],
    }
    assert "не российский" in ps.render(report)


class _Answer:
    def __init__(self, body: bytes, content_type: str):
        self.status, self.headers, self._body = 200, {"Content-Type": content_type}, body

    def geturl(self):
        return "https://www.tutu.ru/robots.txt"

    def read(self, _n):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_robots_txt_that_disallows_captcha_is_not_a_refusal(monkeypatch):
    # tutu.ru robots.txt has «Disallow: /captcha»; the probe of 27.10 called it a captcha page
    answer = _Answer(b"User-agent: *\nDisallow: /captcha\n", "text/plain")
    monkeypatch.setattr(ps.urllib.request, "urlopen", lambda *a, **k: answer)
    r = ps.get("https://www.tutu.ru/robots.txt")
    assert r["protection"] == []
    assert r["verdict"] == "ок"


def test_small_html_captcha_page_is_still_a_refusal(monkeypatch):
    answer = _Answer(b"<html>captcha</html>", "text/html; charset=utf-8")
    monkeypatch.setattr(ps.urllib.request, "urlopen", lambda *a, **k: answer)
    assert ps.get("https://example.ru/").get("verdict", "").startswith("отказ")


def test_timeout_and_reset_are_told_apart():
    # a reset in 0.4 s is a block by IP; a timeout may be a slow route
    assert ps._short("URLError: <urlopen error timed out>") == "таймаут"
    assert ps._short("ConnectionResetError: [Errno 104] Connection reset by peer") == (
        "сброс соединения"
    )
