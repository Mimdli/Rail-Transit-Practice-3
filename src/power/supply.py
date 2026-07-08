"""供电状态模块 — 模拟供电条件对牵引能力的影响"""

from enum import Enum


class PowerStatus(Enum):
    """供电状态"""
    NORMAL = "供电正常"
    LOW_VOLTAGE = "电压低"
    POWER_OFF = "断电"
    RECOVERING = "故障恢复"


class PowerSupply:
    """供电状态管理器"""

    def __init__(self):
        self.status: PowerStatus = PowerStatus.NORMAL
        self._timer: float = 0.0

    def set_status(self, status: PowerStatus):
        """设置供电状态"""
        self.status = status
        if status == PowerStatus.RECOVERING:
            self._timer = 3.0  # 恢复需要 3 秒

    def get_traction_limit(self) -> float:
        """获取牵引能力限制系数 (0.0 ~ 1.0)"""
        if self.status == PowerStatus.NORMAL:
            return 1.0
        elif self.status == PowerStatus.LOW_VOLTAGE:
            return 0.5
        elif self.status == PowerStatus.POWER_OFF:
            return 0.0
        elif self.status == PowerStatus.RECOVERING:
            return 0.3
        return 1.0

    def step(self, dt: float):
        """步进更新"""
        if self.status == PowerStatus.RECOVERING:
            self._timer -= dt
            if self._timer <= 0:
                self.status = PowerStatus.NORMAL

    def can_traction(self) -> bool:
        """是否允许牵引"""
        return self.status not in (PowerStatus.POWER_OFF,)

    def reset(self):
        """重置供电状态"""
        self.status = PowerStatus.NORMAL
        self._timer = 0.0
