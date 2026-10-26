from app_core.core import Smoother


def test_core_runs_without_ros():
    s = Smoother(1.0)
    assert [s.update(x) for x in (1.0, 2.0)] == [1.0, 2.0]
