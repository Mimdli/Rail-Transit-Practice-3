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
"""

from src.common.car_config import CouplerConfig
from src.common.car_state import CarState


def calc_coupler_force(car_i: CarState, car_j: CarState,
                       config: CouplerConfig,
                       nominal_distance: float) -> float:
    """计算相邻两节车之间的车钩力。

    车钩连接 car_i（前方）和 car_j（后方）。

    Args:
        car_i: 前车状态。
        car_j: 后车状态。
        config: 车钩参数。
        nominal_distance: 名义车距 (m)，即正常连接时两车参考点的间距（通常为车长）。

    Returns:
        车钩力 (N)。正值 = 拉伸（车钩将后车向前拉），负值 = 压缩。

    Note:
        假设 car_i 和 car_j 的位置可比较（同一坐标系）。
        在 Pipeline 中，位置会被转换为线路绝对里程后再调用此函数。
    """
    # 实际车距
    actual_distance = _get_position_delta(car_i, car_j)

    # Δx = 实际车距 - 名义车距
    delta_x = actual_distance - nominal_distance

    # Δv = 后方速度 - 前方速度
    delta_v = car_j.velocity - car_i.velocity

    return _calc_coupler_force_raw(delta_x, delta_v, config)


def _calc_coupler_force_raw(delta_x: float, delta_v: float,
                            config: CouplerConfig) -> float:
    """车钩力核心计算（纯物理公式，不依赖 CarState）。

    此函数供 Pipeline 直接调用（Pipeline 已计算好 Δx 和 Δv），
    也供 calc_coupler_force 内部使用。
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


def _get_position_delta(car_i: CarState, car_j: CarState) -> float:
    """计算两车参考点之间的实际距离 (m)。

    简化实现：假设两车在同一段内，直接用 offset 差值。
    在 Pipeline 中，位置会被预先转换为线路绝对里程。
    """
    # 支持两种位置表示:
    # 1. TrackPosition: 使用 offset 差值
    # 2. 纯 float（绝对里程）: 直接用差值
    pos_i = car_i.position
    pos_j = car_j.position

    if hasattr(pos_i, 'offset') and hasattr(pos_j, 'offset'):
        # TrackPosition — 假设同段
        return pos_j.offset - pos_i.offset
    else:
        # float — 绝对里程
        return float(pos_j) - float(pos_i)
