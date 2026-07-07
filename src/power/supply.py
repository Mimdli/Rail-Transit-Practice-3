"""供电状态模块 — TODO: 实现供电条件对牵引能力的影响"""

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

    def set_status(self, status: PowerStatus):
        """设置供电状态"""
        raise NotImplementedError

    def get_traction_limit(self) -> float:
        """获取牵引能力限制系数"""
        raise NotImplementedError

    def step(self, dt: float):
        """步进更新"""
        raise NotImplementedError

    def can_traction(self) -> bool:
        """是否允许牵引"""
        raise NotImplementedError

    def reset(self):
        """重置供电状态"""
        raise NotImplementedError
