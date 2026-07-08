"""信号模块单元测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.signal.system import SignalSystem, SignalAspect, aspect_from_protocol
from src.track.data import Signal


def test_default_aspect_is_green():
    """没有信号机约束时，当前位置默认按绿灯处理。"""
    ss = SignalSystem()
    aspect = ss.get_aspect_at(0.0, [])
    assert aspect == SignalAspect.GREEN


def test_red_signal_ahead():
    """未配置状态的前方信号机默认按红灯防护。"""
    ss = SignalSystem()
    signals = [Signal("S01", 150.0, "up")]
    has_red = ss.check_red_signal_ahead(0.0, signals)
    assert has_red


def test_signal_aspect_can_be_configured():
    """手动设置信号机显示状态后，当前位置查询应返回该状态。"""
    ss = SignalSystem()
    signals = [Signal("S01", 100.0, "up")]
    ss.set_signal_aspect("S01", SignalAspect.YELLOW)

    aspect = ss.get_aspect_at(100.0, signals)

    assert aspect == SignalAspect.YELLOW


def test_yellow_signal_is_not_red_protection():
    """黄灯只触发限速，不应被红灯防护逻辑拦截。"""
    ss = SignalSystem()
    signals = [Signal("S01", 150.0, "up")]
    ss.set_signal_aspect("S01", SignalAspect.YELLOW)

    has_red = ss.check_red_signal_ahead(0.0, signals)

    assert not has_red


def test_no_red_signal_too_far():
    """超出前方防护距离的红灯不影响当前列车。"""
    ss = SignalSystem()
    signals = [Signal("S01", 500.0, "up")]
    has_red = ss.check_red_signal_ahead(0.0, signals, look_ahead=200.0)
    assert not has_red


def test_yellow_speed_limit():
    """前方最近信号为黄灯时，有效限速取线路限速和黄灯限速的较小值。"""
    ss = SignalSystem()
    signals = [Signal("S01", 100.0, "up")]
    ss.set_signal_aspect("S01", SignalAspect.YELLOW)

    limit = ss.get_effective_speed_limit(0.0, 22.0, signals)

    assert limit == ss.yellow_speed_limit


def test_green_signal_keeps_track_speed_limit():
    """前方最近信号为绿灯时，有效限速保持线路限速。"""
    ss = SignalSystem()
    signals = [Signal("S01", 100.0, "up")]
    ss.set_signal_aspect("S01", SignalAspect.GREEN)

    limit = ss.get_effective_speed_limit(0.0, 22.0, signals)

    assert limit == 22.0


def test_occupancy_without_front_train_sets_green():
    """没有前车占用闭塞分区时，自动生成的信号均为绿灯。"""
    ss = SignalSystem()
    signals = [
        Signal("S01", 100.0, "up"),
        Signal("S02", 250.0, "up"),
    ]

    ss.update_aspects_by_occupancy(signals, [])

    assert ss.get_signal_aspect(signals[0]) == SignalAspect.GREEN
    assert ss.get_signal_aspect(signals[1]) == SignalAspect.GREEN


def test_occupied_next_block_sets_red():
    """前车占用当前信号机防护的下一闭塞分区时，该信号机显示红灯。"""
    ss = SignalSystem()
    signals = [
        Signal("S01", 100.0, "up"),
        Signal("S02", 250.0, "up"),
        Signal("S03", 500.0, "up"),
    ]

    ss.update_aspects_by_occupancy(signals, [180.0])

    assert ss.get_signal_aspect(signals[0]) == SignalAspect.RED


def test_occupied_second_block_sets_yellow():
    """前车占用再下一个闭塞分区时，当前信号机显示黄灯预告。"""
    ss = SignalSystem()
    signals = [
        Signal("S01", 100.0, "up"),
        Signal("S02", 250.0, "up"),
        Signal("S03", 500.0, "up"),
    ]

    ss.update_aspects_by_occupancy(signals, [300.0])

    assert ss.get_signal_aspect(signals[0]) == SignalAspect.YELLOW
    assert ss.get_signal_aspect(signals[1]) == SignalAspect.RED


def test_protocol_aspect_mapping_uses_three_states():
    assert aspect_from_protocol(0x04) == SignalAspect.GREEN
    assert aspect_from_protocol(0x02) == SignalAspect.YELLOW
    assert aspect_from_protocol(0x03) == SignalAspect.YELLOW
    assert aspect_from_protocol(0x01) == SignalAspect.RED
    assert aspect_from_protocol(0x0A) == SignalAspect.RED
    assert aspect_from_protocol(0x99) == SignalAspect.RED


def test_set_signal_aspect_from_protocol():
    ss = SignalSystem()
    signal = Signal("S01", 100.0, "up")

    ss.set_signal_aspect_from_protocol("S01", 0x03)

    assert ss.get_signal_aspect(signal) == SignalAspect.YELLOW


if __name__ == "__main__":
    test_default_aspect_is_green()
    test_red_signal_ahead()
    test_signal_aspect_can_be_configured()
    test_yellow_signal_is_not_red_protection()
    test_no_red_signal_too_far()
    test_yellow_speed_limit()
    test_green_signal_keeps_track_speed_limit()
    test_occupancy_without_front_train_sets_green()
    test_occupied_next_block_sets_red()
    test_occupied_second_block_sets_yellow()
    test_protocol_aspect_mapping_uses_three_states()
    test_set_signal_aspect_from_protocol()
    print("All signal tests passed!")
