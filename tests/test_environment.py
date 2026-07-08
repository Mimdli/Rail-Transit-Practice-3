"""MockEnvironment 单元测试

测试范围:
    1. 基础粘着系数（按天气类型）
    2. 隧道/露天粘着系数差异
    3. 无 track 引用时的兼容行为
    4. 无 pos 时的兼容行为
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.track_position import TrackPosition, MockTrackQuery
from src.vehicle.environment import WeatherType, MockEnvironment


# ═══════════════════════════════════════════════════════════════
# 基础粘着系数测试
# ═══════════════════════════════════════════════════════════════

def test_dry_adhesion():
    """干燥天气：基础粘着系数 0.18"""
    env = MockEnvironment(WeatherType.DRY)
    assert env.get_adhesion_coefficient() == 0.18


def test_rain_adhesion():
    """雨天：基础粘着系数 0.10"""
    env = MockEnvironment(WeatherType.RAIN)
    assert env.get_adhesion_coefficient() == 0.10


def test_snow_adhesion():
    """雪天：基础粘着系数 0.06"""
    env = MockEnvironment(WeatherType.SNOW)
    assert env.get_adhesion_coefficient() == 0.06


# ═══════════════════════════════════════════════════════════════
# 隧道/露天差异
# ═══════════════════════════════════════════════════════════════

def test_open_air_no_reduction():
    """露天段（Seg 1）：粘着系数不衰减"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY, track)
    pos = TrackPosition(segment_id=1, offset=500.0)  # Seg 1: 露天
    mu = env.get_adhesion_coefficient(pos)
    assert mu == 0.18  # 露天，不衰减


def test_tunnel_reduction():
    """隧道段（Seg 2）：粘着系数衰减 20%（0.18 → 0.144）"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY, track)
    pos = TrackPosition(segment_id=2, offset=500.0)  # Seg 2: 隧道
    mu = env.get_adhesion_coefficient(pos)
    assert abs(mu - 0.144) < 0.001  # 0.18 * 0.8 = 0.144


def test_tunnel_rain_combined():
    """隧道+雨天：基础 0.10 → 隧道衰减 0.10*0.8 = 0.08"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.RAIN, track)
    pos = TrackPosition(segment_id=2, offset=500.0)
    mu = env.get_adhesion_coefficient(pos)
    assert abs(mu - 0.08) < 0.001


def test_open_air_after_tunnel():
    """Seg 3（隧道后露天）：粘着系数恢复为全局值"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.DRY, track)
    pos = TrackPosition(segment_id=3, offset=500.0)  # Seg 3: 露天
    mu = env.get_adhesion_coefficient(pos)
    assert mu == 0.18


# ═══════════════════════════════════════════════════════════════
# 兼容性测试
# ═══════════════════════════════════════════════════════════════

def test_no_track_no_reduction():
    """无 track 引用时，即使传入隧道位置也不衰减（安全回退）"""
    env = MockEnvironment(WeatherType.DRY)  # 不传 track
    pos = TrackPosition(segment_id=2, offset=500.0)  # 隧道位置
    mu = env.get_adhesion_coefficient(pos)
    assert mu == 0.18  # 无法判断隧道，返回全局值


def test_no_position_returns_global():
    """不传位置参数时返回全局粘着系数"""
    track = MockTrackQuery()
    env = MockEnvironment(WeatherType.RAIN, track)
    mu = env.get_adhesion_coefficient()  # pos=None
    assert mu == 0.10


# ═══════════════════════════════════════════════════════════════
# 运行所有测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_dry_adhesion()
    test_rain_adhesion()
    test_snow_adhesion()
    test_open_air_no_reduction()
    test_tunnel_reduction()
    test_tunnel_rain_combined()
    test_open_air_after_tunnel()
    test_no_track_no_reduction()
    test_no_position_returns_global()
    print("✅ 所有 9 个环境系统测试通过")
