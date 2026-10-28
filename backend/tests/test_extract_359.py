"""#359: children after a dash, a child named again, «двухлетний» outside a child phrase."""

import pytest

from api.extract import children_uncounted, extract


def kids(text: str) -> list[int] | None:
    return extract(text)[0].get("children_ages")


@pytest.mark.parametrize(
    "text",
    [
        "наши дети - сын 4 и дочь 9",
        "наши дети — сын 4 и дочь 9",
        "наши дети: сын 4 и дочь 9",
        "сын 4 и дочь 9",
    ],
)
def test_children_named_after_a_dash_are_the_list(text):
    assert kids(text) == [4, 9]


@pytest.mark.parametrize(
    "text",
    [
        "дети: 5 и 8 лет. Ребёнок 5 лет плохо переносит перелёт",
        "дети: 5 и 8 лет, сын любит море",
        "Сочи в ноябре, дети: 5 и 8 лет. Сын 5 лет плохо переносит перелёт",
    ],
)
def test_a_child_named_again_is_not_a_new_child(text):
    assert kids(text) == [5, 8]


def test_a_son_named_twice_is_one_child():
    assert kids("с двухлетнего возраста сын плавает, едем с сыном") == [7]


def test_an_age_said_later_fills_the_counted_child():
    found, notes = extract("едем с сыном. Сын 5 лет, любит море")
    assert found["children_ages"] == [5]
    assert not any(n.startswith("возраст ребёнка") for n in notes)


def test_two_year_experience_is_no_child_of_two():
    text = "двухлетний опыт путешествий с детьми"
    assert kids(text) == [7, 7]  # «с детьми» without a number: the guess, then a question
    assert children_uncounted(text)


@pytest.mark.parametrize(
    ("text", "ages"),
    [
        ("сын 5 лет и дочь 5 лет", [5, 5]),
        ("сын и дочь", [7, 7]),
        ("сын 4 лет и сын 6 лет", [4, 6]),
        ("с сыном 4 лет и дочкой 9 лет", [4, 9]),
        ("внук и внучка", [7, 7]),
        ("дети с нами — двое, 5 и 8 лет", [5, 8]),
    ],
)
def test_different_children_stay(text, ages):
    assert kids(text) == ages
