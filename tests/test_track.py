"""轨道线路模块单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.track.loader import TrackLoader
from src.track.data import TrackData


def test_demo_data_loaded():
    loader = TrackLoader()
    td = loader.load_demo_data()
    assert len(td.stations) > 0
    assert len(td.platforms) > 0
    assert len(td.segments) > 0
    assert len(td.speed_limits) > 0
    assert len(td.gradients) > 0
    assert len(td.signals) > 0


def test_get_speed_limit():
    loader = TrackLoader()
    td = loader.load_demo_data()
    limit = td.get_speed_limit_at(500.0)
    assert limit > 0


def test_get_gradient():
    loader = TrackLoader()
    td = loader.load_demo_data()
    grad = td.get_gradient_at(300.0)
    assert grad != 0.0


def test_get_nearest_station_ahead():
    loader = TrackLoader()
    td = loader.load_demo_data()
    station = td.get_nearest_station_ahead(100.0)
    assert station is not None
    assert station.position > 100.0


def test_total_length():
    loader = TrackLoader()
    td = loader.load_demo_data()
    assert td.total_length() > 0


def test_get_platform_side():
    loader = TrackLoader()
    td = loader.load_demo_data()
    side = td.get_platform_side_at(0.0)
    assert side == "right"


if __name__ == "__main__":
    test_demo_data_loaded()
    test_get_speed_limit()
    test_get_gradient()
    test_get_nearest_station_ahead()
    test_total_length()
    test_get_platform_side()
    print("All track tests passed!")
