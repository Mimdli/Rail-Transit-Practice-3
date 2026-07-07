"""运行评价器 — 计算运行评价指标"""

from src.logger.recorder import Recorder


class Evaluator:
    """运行评价器"""

    def __init__(self):
        self.max_speed: float = 0.0          # 最高速度 (m/s)
        self.stop_errors: list = []          # 停车误差列表 (m)

    def update_max_speed(self, speed: float):
        """更新最高速度"""
        if speed > self.max_speed:
            self.max_speed = speed

    def record_stop(self, target_position: float, actual_position: float):
        """记录一次停车误差"""
        error = abs(target_position - actual_position)
        self.stop_errors.append(error)

    def evaluate(self, recorder: Recorder) -> dict:
        """综合评价运行结果"""
        summary = recorder.get_summary()
        avg_stop_error = sum(self.stop_errors) / len(self.stop_errors) if self.stop_errors else 0.0

        return {
            "最高速度 (km/h)": self.max_speed * 3.6,
            "平均停车误差 (m)": round(avg_stop_error, 2),
            "最大停车误差 (m)": round(max(self.stop_errors), 2) if self.stop_errors else 0.0,
            "超速次数": summary["超速次数"],
            "红灯违规次数": summary["红灯违规次数"],
            "紧急制动次数": summary["紧急制动次数"],
            "安全事件总数": summary["超速次数"] + summary["红灯违规次数"] + summary["紧急制动次数"],
        }

    def reset(self):
        """重置评价器"""
        self.max_speed = 0.0
        self.stop_errors.clear()
