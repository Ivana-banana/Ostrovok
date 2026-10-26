import pytest
from app_core.core import Smoother


def test_first_value_passes_through():
    assert Smoother(0.5).update(4.0) == 4.0


def test_smoothing():
    s = Smoother(0.5)
    s.update(0.0)
    assert s.update(2.0) == 1.0


def test_bad_alpha():
    with pytest.raises(ValueError):
        Smoother(0.0)
