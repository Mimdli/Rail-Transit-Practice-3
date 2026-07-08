"""TrackPosition 单元测试 — 阶段 1"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.track_position import (
    TrackPosition, MockTrackQuery, MockSeg, ITrackQuery
)


def test_create_position():
    """创建 TrackPosition 并验证字段。"""
    p = TrackPosition(segment_id=1, offset=500.0)
    assert p.segment_id == 1
    assert p.offset == 500.0


def test_advance_within_segment():
    """在同一段内推进，offset 正确增加。"""
    track = MockTrackQuery()
    p = TrackPosition(segment_id=1, offset=0.0)
    p2 = track.advance_position(p, 500.0)
    assert p2.segment_id == 1
    assert p2.offset == 500.0


def test_advance_cross_segment_boundary():
    """跨段边界推进，segment_id 切换正确。"""
    track = MockTrackQuery()
    # Seg 1 长 1000m，从 offset=800 推进 300m 应跨入 Seg 2
    p = TrackPosition(segment_id=1, offset=800.0)
    p2 = track.advance_position(p, 300.0)
    assert p2.segment_id == 2
    assert abs(p2.offset - 100.0) < 0.001  # 800+300-1000 = 100


def test_advance_backward():
    """反向推进。"""
    track = MockTrackQuery()
    p = TrackPosition(segment_id=2, offset=100.0)
    p2 = track.advance_position(p, -150.0)
    # 100-150 = -50, 应退入 Seg 1 offset=950
    assert p2.segment_id == 1
    assert abs(p2.offset - 950.0) < 0.001


def test_advance_clamp_to_start():
    """推进超出起点，裁剪到 0。"""
    track = MockTrackQuery()
    p = TrackPosition(segment_id=1, offset=10.0)
    p2 = track.advance_position(p, -100.0)
    assert p2.segment_id == 1
    assert p2.offset == 0.0


def test_advance_clamp_to_end():
    """推进超出终点，裁剪到终点。"""
    track = MockTrackQuery()
    p = TrackPosition(segment_id=3, offset=900.0)
    p2 = track.advance_position(p, 200.0)
    # 应停在 Seg 3 末端
    assert p2.segment_id == 3
    assert p2.offset == 1000.0


def test_get_speed_limit():
    """查询各段限速。"""
    track = MockTrackQuery()
    assert track.get_speed_limit(TrackPosition(1, 0.0)) == 22.22
    assert track.get_speed_limit(TrackPosition(2, 0.0)) == 16.67
    assert track.get_speed_limit(TrackPosition(3, 0.0)) == 22.22


def test_get_gradient():
    """查询各段坡度。"""
    track = MockTrackQuery()
    assert track.get_gradient(TrackPosition(1, 0.0)) == 0.0
    assert track.get_gradient(TrackPosition(2, 0.0)) == 30.0
    assert track.get_gradient(TrackPosition(3, 0.0)) == -15.0


def test_get_is_tunnel():
    """查询各段隧道状态。"""
    track = MockTrackQuery()
    assert track.get_is_tunnel(TrackPosition(1, 0.0)) is False
    assert track.get_is_tunnel(TrackPosition(2, 0.0)) is True
    assert track.get_is_tunnel(TrackPosition(3, 0.0)) is False


def test_mock_seg_fields():
    """MockSeg 字段完整性。"""
    seg = MockSeg(id=1, length=1000.0, limit=22.22, gradient=0.0, tunnel=False)
    assert seg.id == 1
    assert seg.length == 1000.0


if __name__ == "__main__":
    test_create_position()
    test_advance_within_segment()
    test_advance_cross_segment_boundary()
    test_advance_backward()
    test_advance_clamp_to_start()
    test_advance_clamp_to_end()
    test_get_speed_limit()
    test_get_gradient()
    test_get_is_tunnel()
    test_mock_seg_fields()
    print("All track_position tests passed!")
