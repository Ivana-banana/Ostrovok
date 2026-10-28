"""#204: an edit and «без трансфера» in one message drop the transfer; a command with «сколько»
is an edit that names the new sum. Each phrase runs with a stub model and without a model (TRAP-1).
"""

import pytest
from talk import say
from test_party_transfer_edit import at, stub_model, transfers

from api.store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


COMPOUND = ["без трансфера и на неделю позже", "уберите трансфер и поездку на неделю позже"]
COMMAND_HOW_MUCH = [
    "уберите трансфер — сколько теперь выйдет?",
    "без трансфера, сколько будет?",
    "удалите трансфер, сколько получится?",
    "исключите трансфер, сколько выйдет?",
]


def run(monkeypatch, store, stage, text, model):
    r = at(store, stage)
    assert transfers(r)
    if model:
        stub_model(monkeypatch, [])
    return r, say(store, text, r.session_id)


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("text", COMPOUND)
def test_compound_edit_drops_transfer(monkeypatch, store, text, stage, model):
    r, r2 = run(monkeypatch, store, stage, text, model)
    assert r2.plan.spec.date_from > r.plan.spec.date_from
    assert not transfers(r2)
    assert all(o.kind != "transfer" for o in r2.cart.items)
    r3 = say(store, "беру", r2.session_id)  # the refusal holds for the next turns
    assert not transfers(r3) and all(o.kind != "transfer" for o in r3.cart.items)


@pytest.mark.parametrize("model", [False, True])
@pytest.mark.parametrize("stage", ["assemble", "checkout"])
@pytest.mark.parametrize("text", COMMAND_HOW_MUCH)
def test_command_with_how_much_is_an_edit(monkeypatch, store, text, stage, model):
    r, r2 = run(monkeypatch, store, stage, text, model)
    assert "Это вопрос" not in r2.reply
    assert not transfers(r2)
    assert all(o.kind != "transfer" for o in r2.cart.items)
    assert "синтетика" in r2.reply
    sold = sum(o.price_rub for o in r2.plan.offers if o.offer_id in r2.plan.selected)
    assert f"{sold:,}".replace(",", " ") in r2.reply.replace(" ", " ").replace(" ", " ")
    if stage == "checkout":
        assert r2.cart.total_rub < r.cart.total_rub
