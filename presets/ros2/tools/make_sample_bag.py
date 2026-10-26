"""Маленький bag для e2e и тестов: /input (std_msgs/Float64), 10 сообщений. Без ROS.

uv run python tools/make_sample_bag.py <каталог>
"""

import shutil
import sys
from pathlib import Path

from rosbags.rosbag2 import StoragePlugin, Writer
from rosbags.typesys import Stores, get_typestore


def write(path: Path, n: int = 10) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    store = get_typestore(Stores.ROS2_HUMBLE)
    Float64 = store.types["std_msgs/msg/Float64"]
    with Writer(path, version=8, storage_plugin=StoragePlugin.SQLITE3) as w:
        conn = w.add_connection("/input", Float64.__msgtype__, typestore=store)
        for i in range(n):
            w.write(
                conn,
                (i + 1) * 100_000_000,
                store.serialize_cdr(Float64(data=float(i)), Float64.__msgtype__),
            )


if __name__ == "__main__":
    write(Path(sys.argv[1]))
