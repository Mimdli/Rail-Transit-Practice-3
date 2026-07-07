"""信号模块单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.signal.system import SignalSystem, SignalAspect
from src.track.data import Signal


def test_default_aspect_is_green():
    ss = SignalSystem()
    aspect = ss.get_aspect_at(0.0, [])
    assert aspect == SignalAspect.GREEN


def test_red_signal_ahead():
    ss = SignalSystem()
    signals = [Signal("S01", 150.0, "up")]
    has_red = ss.check_red_signal_ahead(0.0, signals)
    assert has_red


def test_no_red_signal_too_far():
    ss = SignalSystem()
    signals = [Signal("S01", 500.0, "up")]
    has_red = ss.check_red_signal_ahead(0.0, signals, look_ahead=200.0)
    assert not has_red


def test_yellow_speed_limit():
    ss = SignalSystem()
    limit = ss.get_effective_speed_limit(0.0, 22.0, [])
    assert limit == 22.0


if __name__ == "__main__":
    test_default_aspect_is_green()
    test_red_signal_ahead()
    test_no_red_signal_too_far()
    test_yellow_speed_limit()
    print("All signal tests passed!")
