"""#534: a network failure of the geocoder is logged and said aloud; a flaky place is asked again."""

import logging

from api import catalog
from test_catalog import _bled_with_roads


def test_trap_6_geocoder_failure_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="api.catalog"):
        assert catalog.lookup_online("Зурбаган") is None  # conftest: no network
    assert "geocoder failed" in caplog.text and "OSError" in caplog.text


def test_trap_6_origin_not_found_says_it_may_be_the_network():
    hub, note = catalog.origin_hub("Зурбаган")
    assert hub["name"] == "Москва"
    assert "или нет сети" in note


def test_trap_6_flaky_place_is_asked_again_after_failed_s(geo, monkeypatch):
    """OSRM was down: the place works at once, but after FAILED_S the road is asked again."""
    now = [1000.0]
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now[0])
    down, _ = _bled_with_roads(geo, None)
    first = catalog.lookup_online("Блед", down)
    assert first["airport"]["road"] is False
    assert catalog.lookup_online("Блед", down) is first  # inside FAILED_S: the process cache
    now[0] += catalog.FAILED_S
    up, _ = _bled_with_roads(geo, [30, 60])
    again = catalog.lookup_online("Блед", up)
    assert again["airport"]["road"] is True
    assert catalog.lookup_online("Блед", up) is again  # a full place stays
    now[0] += 10 * catalog.FAILED_S
    assert catalog.lookup_online("Блед", down) is again
