"""#163: «тепло» and a refusal in the next clause; «тепло» in summer is no filter (choice kept)."""

from api import inspire
from api.contracts import TripSpec
from api.extract import extract


def test_refusal_after_but_is_not_of_warmth():
    wishes = extract("Хочу туда, где тепло, но не хочу далеко лететь")[0].get("wishes", [])
    assert "тепло" in wishes


def test_refusal_in_the_clause_of_warmth_still_counts():
    assert "тепло" not in extract("тепло не люблю, хочу в горы")[0].get("wishes", [])


def test_warmth_is_a_theme_in_the_cold_months_only():
    """#114: in July every place is warm, so «тёплые края» picks no place by the climate."""
    assert not inspire._themed(TripSpec(month=7, wishes=["тепло"]))
    assert inspire._themed(TripSpec(month=1, wishes=["тепло"]))
