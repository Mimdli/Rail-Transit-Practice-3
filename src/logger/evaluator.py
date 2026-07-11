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
        safety_events = (
            summary["超速次数"] + summary["红灯违规次数"]
            + summary["紧急制动次数"] + summary["碰撞次数"]
        )
        score = max(0.0, 100.0
                    - summary["超速次数"] * 5.0
                    - summary["红灯违规次数"] * 20.0
                    - summary["紧急制动次数"] * 10.0
                    - summary["碰撞次数"] * 30.0
                    - avg_stop_error * 2.0)
        grade = "优秀" if score >= 90 else (
            "良好" if score >= 80 else "合格" if score >= 60 else "不合格")

        return {
            "综合得分": round(score, 1),
            "评价等级": grade,
            "最高速度 (km/h)": self.max_speed * 3.6,
            "平均停车误差 (m)": round(avg_stop_error, 2),
            "最大停车误差 (m)": round(max(self.stop_errors), 2) if self.stop_errors else 0.0,
            "超速次数": summary["超速次数"],
            "红灯违规次数": summary["红灯违规次数"],
            "紧急制动次数": summary["紧急制动次数"],
            "碰撞次数": summary["碰撞次数"],
            "安全事件总数": safety_events,
            "严重事件总数": summary["严重事件数"],
        }

    def reset(self):
        """重置评价器"""
        self.max_speed = 0.0
        self.stop_errors.clear()
