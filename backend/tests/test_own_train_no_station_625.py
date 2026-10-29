"""#625: own tickets to a place without a station (Kaliningrad), then «ехать поездом до отеля?»
hit a TypeError in offers._hub_road (station_km is None). No station: no transfer is invented."""

from talk import say

from api.store import SessionStore

OWN = (
    "Калининград из Москвы 11–13 декабря, 2 взрослых, у нас свои билеты, "
    "прилёт 11 декабря в 14:00, обратно 13 декабря в 18:00"
)


def test_own_tickets_train_question_without_station_has_no_failed_source(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    r = say(store, OWN)
    r = say(store, "нам нужно ехать поездом до отеля?", r.session_id)
    assert r.reply and "не ответил" not in r.reply
    assert not [i for i in r.plan.issues if "Трансфер" in i.text and "не ответил" in i.text]
