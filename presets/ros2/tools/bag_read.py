"""Чтение rosbag2 без ROS (библиотека rosbags): темы, число сообщений, первые значения.

uv run python tools/bag_read.py <каталог bag> [тема]
"""

import sys
from pathlib import Path

from rosbags.highlevel import AnyReader


def summary(bag: Path) -> dict[str, int]:
    with AnyReader([bag]) as reader:
        return {c.topic: c.msgcount for c in reader.connections}


def values(bag: Path, topic: str) -> list[float]:
    out = []
    with AnyReader([bag]) as reader:
        conns = [c for c in reader.connections if c.topic == topic]
        for conn, _, raw in reader.messages(connections=conns):
            out.append(reader.deserialize(raw, conn.msgtype).data)
    return out


def main(argv: list[str]) -> int:
    bag = Path(argv[0])
    for topic, n in summary(bag).items():
        print(f"{topic}: {n}")
    if len(argv) > 1:
        print(values(bag, argv[1])[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
