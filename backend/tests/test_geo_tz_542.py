"""#542: a geocoder record without a timezone (a country: «Новая Зеландия») still gives a place."""

from api import catalog


def test_trap_2_record_without_timezone_takes_the_airport_zone(geo):
    base = geo("Новая Зеландия", country="Новая Зеландия")

    def fetch(url: str, params: dict) -> dict:
        out = base(url, params)
        for r in out.get("results", []):
            r.pop("timezone")  # Open-Meteo gives no timezone for a country record
        return out

    place = catalog.lookup_online("Новая Зеландия", fetch)
    assert place is not None
    assert place["tz"] == place["airport"]["tz"]
