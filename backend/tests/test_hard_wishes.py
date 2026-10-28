"""#338: hard conditions of the request reach the spec; the reply names what no data checks."""

import pytest

from api.extract import extract

CASES = [
    # F05: wheelchair is the existing «тяжело ходить» wish (the optimizer checks lift data)
    ("Едем на море в Анапу в июле, бабушка на коляске.", "тяжело ходить"),
    ("Едем в Сочи с детской коляской на неделю.", "детская коляска"),
    ("Прилетаю в Минск 14 ноября, я веганка.", "веганское питание"),
    (
        "Сапсан приходит в Питер в 00:35, заселиться надо сразу, а не ждать до двух.",
        "поздний заезд",
    ),
    ("Подмосковье с годовалым сыном, обязательно детская кроватка в номере.", "детская кроватка"),
    ("Командировка в Тюмень, нужен отель у Технопарка.", "рядом: Технопарка"),
    ("Командировка в Тюмень, завтрак с 7 утра.", "завтрак с 07:00"),
]


@pytest.mark.parametrize(("text", "wish"), CASES)
def test_hard_condition_reaches_the_spec(text, wish):
    found, _ = extract(text)
    assert wish in found.get("wishes", []), found


def test_refused_or_city_is_no_hard_wish():
    found, _ = extract("Едем у моря, без кроватки, из Москвы в Сочи, живём у Москвы")
    assert "детская кроватка" not in found.get("wishes", [])
    assert not [w for w in found.get("wishes", []) if w.startswith("рядом:") and "Москв" in w]


def test_reply_names_the_hard_wishes_as_not_checked(tmp_path):
    from talk import say

    from api.store import SessionStore

    r = say(SessionStore(tmp_path / "s.db"), CASES[4][0] + " Выходные 7–8 ноября, до 30 тысяч.")
    assert "Не проверяю по данным: детская кроватка" in r.reply, r.reply
