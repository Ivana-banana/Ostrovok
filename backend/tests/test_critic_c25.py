"""#504: critic card C25 (eval/cases_critic.json). «не из Москвы, а из Кинешмы» changes the origin.
Offline, rules only (TRAP-1)."""

import pytest
from talk import say

from api.extract import extract
from api.store import SessionStore

C25 = "Сочи на 5 ночей с 10 ноября вдвоём, самолётом из Москвы"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIGACHAT_AUTH_KEY", raising=False)
    return SessionStore(tmp_path / "sessions.db")


@pytest.mark.parametrize(
    ("text", "origin"),
    [
        ("мы вылетаем не из Москвы, а из Казани", "Казань"),
        ("вылет не из Санкт-Петербурга, а из Пскова, едем в Сочи", "Псков"),
        ("не от Москвы, а от Твери", "Тверь"),
        ("из Москвы в Казань", "Москва"),
    ],
)
def test_not_from_x_but_from_y_gives_y(text, origin):
    assert extract(text)[0]["origin"] == origin


def test_not_from_x_alone_is_no_origin_and_no_destination():
    found = extract("не из Москвы")[0]
    assert "origin" not in found and "destinations" not in found


def test_C25_correction_changes_the_origin(store):
    r = say(store, C25)
    assert r.plan.spec.origin == "Москва"
    r = say(store, "мы вылетаем не из Москвы, а из Кинешмы", r.session_id)
    # pass of the card: the origin changed, or the reply names Кинешма and the hub it uses
    assert r.plan.spec.origin != "Москва" or ("Кинешм" in r.reply and "Москв" in r.reply)
    assert not r.reply.startswith("Напишите «беру»")  # not the hint alone


def test_C25_a_far_city_says_its_hub(store):
    r = say(store, C25)
    r = say(store, "вылетаем из Подольска", r.session_id)
    assert "Подольск" in r.reply and "Москв" in r.reply
