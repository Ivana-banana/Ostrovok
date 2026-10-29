"""#383: a refusal of a wish that no data checks drops it: «кухня не важна», «без удобств»."""

import pytest

from api.extract import extract


@pytest.mark.parametrize(
    ("text", "wish"),
    [
        ("кухня не важна, на море в июле", "вино и еда"),
        ("без удобств, в палатке на Байкал в июле", "с удобствами"),
        ("романтика не нужна, Сочи в мае", "романтика"),
    ],
)
def test_refused_free_wish_is_dropped(text, wish):
    assert wish not in extract(text)[0].get("wishes", [])


@pytest.mark.parametrize(
    ("text", "wish"),
    [
        ("на море в июле, хочу вкусную кухню", "вино и еда"),
        ("Сочи, с удобствами", "с удобствами"),
        ("Сочи, без вина не поеду", "вино и еда"),
    ],
)
def test_wanted_free_wish_stays(text, wish):
    assert wish in extract(text)[0].get("wishes", [])
