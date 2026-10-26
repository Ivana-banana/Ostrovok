import bag_read
import make_sample_bag


def test_bag_roundtrip_without_ros(tmp_path):
    bag = tmp_path / "bag"
    make_sample_bag.write(bag, n=5)
    assert bag_read.summary(bag) == {"/input": 5}
    assert bag_read.values(bag, "/input") == [0.0, 1.0, 2.0, 3.0, 4.0]
