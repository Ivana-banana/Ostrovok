"""#490, #566: the switches of ChatRequest pick the sources per request, both on by default.

No network: the real transport is a fake adapter, the model is a fake _complete.
"""

import pytest
from fastapi.testclient import TestClient

from api import llm, offers
from api.contracts import TransportOffer
from api.main import app, get_store
from api.offers import SyntheticTransport
from api.store import SessionStore

SOCHI = (
    "Сочи, 3 ночи с 20 ноября, двое взрослых, отель до 40 тысяч за всё, нужен завтрак, из Москвы."
)
LLM_ENV = {"LLM_BASE_URL": "http://llm.test/v1", "LLM_MODEL": "test", "LLM_API_KEY": "test-key"}


class Real:
    """A real transport stand-in: the synthetic legs with synthetic=False; counts the calls."""

    def __init__(self) -> None:
        self.calls = 0

    def legs(self, spec, origin, place) -> list[TransportOffer]:
        self.calls += 1
        legs = SyntheticTransport().legs(spec, origin, place)
        return [x.model_copy(update={"synthetic": False}) for x in legs]


@pytest.fixture
def client(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def real(monkeypatch) -> Real:
    adapter = Real()
    monkeypatch.setattr(offers, "REAL_TRANSPORT", adapter)  # as main.py with a token
    return adapter


def transport(reply: dict) -> list[dict]:
    return [o for o in reply["plan"]["offers"] if o["kind"] == "transport"]


class LiveHotels:
    """A live hotel layer stand-in: counts the calls, falls back to `base` like Live101Hotels."""

    def __init__(self, base) -> None:
        self.base = base
        self.calls = 0

    def hotels(self, spec, place, now):
        self.calls += 1
        return self.base.hotels(spec, place, now)


def test_trap7_real_data_off_gives_synthetic_even_with_token(client, real, monkeypatch):
    live = LiveHotels(offers.HOTELS)
    monkeypatch.setattr(offers, "HOTELS", live)
    reply = client.post("/api/chat", json={"message": SOCHI, "real_data": False}).json()
    assert transport(reply) and all(o["synthetic"] for o in transport(reply))
    assert real.calls == 0 and live.calls == 0  # #566: «Синтетика» drops the live hotels too


def test_trap7_real_data_by_default_uses_the_real_adapter_for_this_request_only(client, real):
    reply = client.post("/api/chat", json={"message": SOCHI}).json()  # #566: on by default
    assert real.calls > 0
    assert transport(reply) and not any(o["synthetic"] for o in transport(reply))
    assert offers.transport() is offers.TRANSPORT  # no global swap after the request


def test_mode_reports_what_the_switches_can_turn_on(client, real, monkeypatch):
    for k, v in LLM_ENV.items():
        monkeypatch.setenv(k, v)
    assert client.get("/api/mode").json() == {"llm": True, "real_data": True}


@pytest.mark.parametrize("on", [False, True])
def test_trap1_llm_switch_off_asks_no_model_even_with_key(client, monkeypatch, on):
    for k, v in LLM_ENV.items():
        monkeypatch.setenv(k, v)
    llm.reset()
    calls = []

    def fake(system, data, name, schema, until) -> None:  # None: the rules answer
        calls.append(name)

    monkeypatch.setattr(llm, "_complete", fake)
    reply = client.post("/api/chat", json={"message": SOCHI, "llm": on}).json()
    assert bool(calls) is on
    assert reply["llm"] is False
