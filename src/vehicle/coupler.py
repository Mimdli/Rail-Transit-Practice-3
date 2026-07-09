"""车钩力计算 — 弹簧-阻尼-间隙模型（纯函数）

阶段 6：计算相邻两节车之间的车钩力。

模型: 弹簧-阻尼-间隙（分段线性）
    Δx = 实际车距 - 名义车距（车长）
    Δv = 相对速度（后方减前方: v_j - v_i）
    slack = 车钩间隙半宽

    若 Δx > +slack:  拉伸区 → F = K × (Δx - slack) + D × Δv  (正值)
    若 |Δx| ≤ slack:  自由区 → F = 0
    若 Δx < -slack:  压缩区 → F = K × (Δx + slack) + D × Δv  (负值)

刚性方程警示: K ≈ 10^7 N/m 是真实物理参数，不可调小。本模块仅负责力计算
（纯函数），不涉及积分。数值稳定性由阶段 7 的微步长解算器保证。

调用约定: Pipeline 层自行完成 TrackPosition → 绝对里程转换，
计算出 Δx 和 Δv 后直接调用 _calc_coupler_force_raw。
"""

from src.common.car_config import CouplerConfig


def _calc_coupler_force_raw(delta_x: float, delta_v: float,
                            config: CouplerConfig) -> float:
    """车钩力核心计算（纯物理公式）。

    Pipeline 层自行完成 TrackPosition → 绝对里程转换，
    计算出 Δx 和 Δv 后直接调用此函数。
    """
    slack = config.slack

    if delta_x > slack:
        # 拉伸区
        force = config.stiffness * (delta_x - slack) + config.damping * delta_v
    elif delta_x < -slack:
        # 压缩区
        force = config.stiffness * (delta_x + slack) + config.damping * delta_v
    else:
        # 自由区（间隙内）—— 无力
        force = 0.0

    # 限幅到最大承受力（防止数值振荡导致的非物理力）
    if force > config.max_force:
        force = config.max_force
    elif force < -config.max_force:
        force = -config.max_force

    return force
