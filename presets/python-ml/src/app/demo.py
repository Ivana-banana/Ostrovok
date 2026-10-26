"""Демо-путь жюри (make demo): вход → модель → ответ с объяснением."""

import sys

from app.contracts import Request
from app.model import predict


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = " ".join(args) or "пример запроса"
    p = predict(Request(text=text))
    print(f"{text!r} → {p.label} ({p.score:.2f}): {p.explanation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
