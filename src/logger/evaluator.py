"""运行评价器 — TODO: 实现运行评价指标计算"""

from src.logger.recorder import Recorder


class Evaluator:
    """运行评价器"""

    def __init__(self):
        self.max_speed: float = 0.0
        self.stop_errors: list = []

    def update_max_speed(self, speed: float):
        """更新最高速度"""
        raise NotImplementedError

    def record_stop(self, target_position: float, actual_position: float):
        """记录一次停车误差"""
        raise NotImplementedError

    def evaluate(self, recorder: Recorder) -> dict:
        """综合评价运行结果"""
        raise NotImplementedError

    def reset(self):
        """重置评价器"""
        raise NotImplementedError
