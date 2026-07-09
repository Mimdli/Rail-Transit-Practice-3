"""车钩力计算单元测试 — 阶段 6

4 个必测工况（配置: K=1e7 N/m, D=1e5 N·s/m, slack=0.02m）
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.common.car_config import CouplerConfig
from src.vehicle.coupler import _calc_coupler_force_raw


# 车钩配置（与方案一致）
CONFIG = CouplerConfig(stiffness=1e7, damping=1e5, slack=0.02, max_force=2e6)


# ═══════════════════════════════════════════════════════════════
# 4 个必测工况
# ═══════════════════════════════════════════════════════════════

def test_case1_tension():
    """工况1 — 拉伸 (Δx = +0.03m, Δv = 0):
    Δx > 0.02 → 拉伸区
    F = 1e7 × (0.03 - 0.02) + 0 = 100 kN
    """
    f = _calc_coupler_force_raw(0.03, 0.0, CONFIG)
    expected = 1e7 * 0.01  # 100,000 N
    assert abs(f - expected) < 1.0, f"Expected {expected}, got {f}"


def test_case2_free_zone():
    """工况2 — 间隙内 (Δx = +0.01m, Δv = 0):
    |0.01| ≤ 0.02 → 自由区
    F = 0
    """
    f = _calc_coupler_force_raw(0.01, 0.0, CONFIG)
    assert f == 0.0, f"Expected 0, got {f}"


def test_case3_compression():
    """工况3 — 压缩 (Δx = -0.04m, Δv = 0):
    -0.04 < -0.02 → 压缩区
    F = 1e7 × (-0.04 + 0.02) = -200 kN
    """
    f = _calc_coupler_force_raw(-0.04, 0.0, CONFIG)
    expected = 1e7 * (-0.04 + 0.02)  # -200,000 N
    assert abs(f - expected) < 1.0, f"Expected {expected}, got {f}"


def test_case4_tension_with_damping():
    """工况4 — 拉伸+阻尼 (Δx = +0.03m, Δv = +0.5m/s):
    Δv > 0 表示后车快于前车（间隙正在闭合），阻尼抵抗相对运动 → 减小恢复力
    F = 1e7 × 0.01 - 1e5 × 0.5 = 100k - 50k = 50 kN
    """
    f = _calc_coupler_force_raw(0.03, 0.5, CONFIG)
    expected = 1e7 * 0.01 - 1e5 * 0.5  # 50,000 N
    assert abs(f - expected) < 1.0, f"Expected {expected}, got {f}"


# ═══════════════════════════════════════════════════════════════
# 额外边界测试
# ═══════════════════════════════════════════════════════════════

def test_negative_damping():
    """压缩 + 负阻尼 (Δx = -0.04, Δv = -0.3):
    Δv < 0 表示前车快于后车（间隙正在扩大），阻尼抵抗 → 减小压缩力幅值
    F = 1e7 × (-0.04 + 0.02) - 1e5 × (-0.3) = -200k + 30k = -170 kN
    """
    f = _calc_coupler_force_raw(-0.04, -0.3, CONFIG)
    expected = 1e7 * (-0.02) - 1e5 * (-0.3)  # -170,000 N
    assert abs(f - expected) < 1.0, f"Expected {expected}, got {f}"


def test_exactly_at_slack_boundary():
    """Δx 恰好等于 slack 时，为自由区边界，F = 0。"""
    f = _calc_coupler_force_raw(0.02, 0.0, CONFIG)
    assert f == 0.0


def test_exactly_at_negative_slack():
    """Δx 恰好等于 -slack 时，F = 0。"""
    f = _calc_coupler_force_raw(-0.02, 0.0, CONFIG)
    assert f == 0.0


def test_large_compression():
    """大幅压缩: Δx = -0.10。"""
    f = _calc_coupler_force_raw(-0.10, 0.0, CONFIG)
    expected = 1e7 * (-0.10 + 0.02)  # -800 kN
    assert abs(f - expected) < 1.0


def test_large_tension_with_velocity():
    """大幅拉伸 + 大相对速度: Δx = 0.05, Δv = 2.0。
    F = 1e7 × (0.05 - 0.02) - 1e5 × 2.0 = 300k - 200k = 100 kN
    """
    f = _calc_coupler_force_raw(0.05, 2.0, CONFIG)
    expected = 1e7 * (0.05 - 0.02) - 1e5 * 2.0  # 100,000 N
    assert abs(f - expected) < 1.0


def test_different_stiffness():
    """不同刚度参数。"""
    config = CouplerConfig(stiffness=5e6, damping=5e4, slack=0.01)
    f = _calc_coupler_force_raw(0.03, 0.0, config)
    # 0.03 > 0.01 → 拉伸区
    expected = 5e6 * (0.03 - 0.01)  # 100 kN
    assert abs(f - expected) < 1.0


if __name__ == "__main__":
    test_case1_tension()
    test_case2_free_zone()
    test_case3_compression()
    test_case4_tension_with_damping()
    test_negative_damping()
    test_exactly_at_slack_boundary()
    test_exactly_at_negative_slack()
    test_large_compression()
    test_large_tension_with_velocity()
    test_different_stiffness()
    print("All coupler tests passed!")
