"""TrainConsist — 编组配置

阶段 4：定义整列车的编组——哪些位置是动车、哪些是拖车。
"""

import copy
from typing import List
from src.common.car_config import CarConfig, MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG


class TrainConsist:
    """列车编组配置。

    按从头到尾的顺序存储每节车的 CarConfig。
    支持 len()、索引访问和动车判断。

    注意：构造函数对每节车的 CarConfig 做深拷贝，因此修改编组中某节车的属性
    （如 mass）不会影响全局预设单例。

    Usage:
        consist = TrainConsist([MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG, MOTOR_CAR_CONFIG])
        assert len(consist) == 3
        assert consist.is_motor(0) == True
        assert consist.is_motor(1) == False
    """

    def __init__(self, cars: List[CarConfig]):
        if len(cars) == 0:
            raise ValueError("编组至少需要 1 节车")
        # 深拷贝每节车的配置，断开对全局单例的引用，
        # 防止 set_load_level 等操作污染 MOTOR_CAR_CONFIG / TRAILER_CAR_CONFIG
        self._cars = [copy.deepcopy(c) for c in cars]

    def __len__(self) -> int:
        """返回编组节数。"""
        return len(self._cars)

    def __getitem__(self, index: int) -> CarConfig:
        """按索引取单车配置（0-based，从头到尾）。"""
        return self._cars[index]

    def is_motor(self, index: int) -> bool:
        """判断第 index 节是否为动车。"""
        return self._cars[index].is_motor

    @property
    def total_mass(self) -> float:
        """整列车总质量 (kg)。"""
        return sum(c.mass for c in self._cars)

    @property
    def total_length(self) -> float:
        """整列车总长度 (m)。"""
        return sum(c.length for c in self._cars)

    @property
    def motor_count(self) -> int:
        """动车节数。"""
        return sum(1 for c in self._cars if c.is_motor)

    @property
    def trailer_count(self) -> int:
        """拖车节数。"""
        return sum(1 for c in self._cars if not c.is_motor)

    def __repr__(self) -> str:
        motors = self.motor_count
        trailers = self.trailer_count
        return f"TrainConsist({motors}M{trailers}T, {len(self)} cars, {self.total_mass/1000:.0f}t)"


# ═══════════════════════════════════════════════════════════════
# 编组预设
# ═══════════════════════════════════════════════════════════════

# 4M2T — 4动2拖混编（典型地铁编组）
# 排列: M - T - M - M - T - M
CONSIST_4M2T = TrainConsist([
    MOTOR_CAR_CONFIG,     # Car 1 (头车, 动车)
    TRAILER_CAR_CONFIG,   # Car 2 (拖车)
    MOTOR_CAR_CONFIG,     # Car 3 (动车)
    MOTOR_CAR_CONFIG,     # Car 4 (动车)
    TRAILER_CAR_CONFIG,   # Car 5 (拖车)
    MOTOR_CAR_CONFIG,     # Car 6 (尾车, 动车)
])

# 6M0T — 全动车编组（高性能模式）
# 排列: M - M - M - M - M - M
CONSIST_6M0T = TrainConsist([
    MOTOR_CAR_CONFIG,
    MOTOR_CAR_CONFIG,
    MOTOR_CAR_CONFIG,
    MOTOR_CAR_CONFIG,
    MOTOR_CAR_CONFIG,
    MOTOR_CAR_CONFIG,
])

# 1M4T — 动力集中编组（类似机车牵引）
# 排列: M - T - T - T - T
CONSIST_1M4T = TrainConsist([
    MOTOR_CAR_CONFIG,     # Car 1 (动车/机车)
    TRAILER_CAR_CONFIG,   # Car 2
    TRAILER_CAR_CONFIG,   # Car 3
    TRAILER_CAR_CONFIG,   # Car 4
    TRAILER_CAR_CONFIG,   # Car 5
])
