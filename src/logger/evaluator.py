"""统一运行评价器：供桌面端、日志系统和 Web ATS 复用。"""

from typing import Optional

from src.logger.recorder import Recorder


class Evaluator:
    """基于结构化日志、回放帧和能耗数据生成六维运行评价。"""

    WEIGHTS = {
        "safety": 0.30,
        "stopAccuracy": 0.20,
        "smoothness": 0.20,
        "energy": 0.15,
        "punctuality": 0.10,
        "operation": 0.05,
    }

    def __init__(self):
        self.max_speed: float = 0.0
        self.stop_errors: list[float] = []

    def update_max_speed(self, speed: float):
        """更新最高速度，单位为 m/s。"""
        if speed > self.max_speed:
            self.max_speed = speed

    def record_stop(self, target_position: float, actual_position: float):
        """记录一次停车误差。"""
        self.stop_errors.append(abs(target_position - actual_position))

    def evaluate(self, recorder: Recorder, *, frames: Optional[list[dict]] = None,
                 regen_ratios: Optional[list[float]] = None) -> dict:
        """按统一口径计算六维指标，未采集维度不参与加权。"""
        summary = recorder.get_summary()
        safety_score = max(
            0.0,
            100.0
            - summary["超速次数"] * 5.0
            - summary["红灯违规次数"] * 20.0
            - summary["紧急制动次数"] * 10.0
            - summary["碰撞次数"] * 30.0,
        )

        avg_stop_error = (
            sum(self.stop_errors) / len(self.stop_errors)
            if self.stop_errors else None
        )
        stop_score = (
            max(0.0, 100.0 - max(0.0, avg_stop_error - 0.5) * 20.0)
            if avg_stop_error is not None else None
        )
        smooth_score, smooth_basis = self.smoothness_metrics(frames or [])

        ratios = [max(0.0, min(1.0, value)) for value in (regen_ratios or [])]
        regen_ratio = sum(ratios) / len(ratios) if ratios else None
        energy_score = (
            min(100.0, 70.0 + regen_ratio * 30.0)
            if regen_ratio is not None else None
        )

        departures = summary["发车次数"]
        arrivals = summary["到站次数"]
        schedule_gap = abs(departures - arrivals)
        punctuality_score = (
            max(0.0, 100.0 - schedule_gap * 10.0)
            if departures or arrivals else None
        )
        command_failures = len([
            event for event in recorder.events
            if event.source == "web-ats" and event.severity == "WARNING"
        ])
        operation_score = max(0.0, 100.0 - command_failures * 5.0)

        dimensions = {
            "safety": round(safety_score, 1),
            "punctuality": self._round_optional(punctuality_score),
            "stopAccuracy": self._round_optional(stop_score),
            "smoothness": self._round_optional(smooth_score),
            "energy": self._round_optional(energy_score),
            "operation": round(operation_score, 1),
        }
        score = self._weighted_score(dimensions)
        grade = self._grade(score)
        safety_events = (
            summary["超速次数"] + summary["红灯违规次数"]
            + summary["紧急制动次数"] + summary["碰撞次数"]
        )
        basis = {
            **smooth_basis,
            "averageStopError": (round(avg_stop_error, 3)
                                 if avg_stop_error is not None else None),
            "regenRatio": round(regen_ratio, 4) if regen_ratio is not None else None,
            "commandFailures": command_failures,
            "scheduleGap": schedule_gap if punctuality_score is not None else None,
        }

        # 同时提供 Web 英文字段与桌面端既有中文字段。
        return {
            "score": score,
            "grade": grade,
            "dimensions": dimensions,
            "evaluationBasis": basis,
            "weights": dict(self.WEIGHTS),
            "综合得分": score,
            "评价等级": grade,
            "最高速度 (km/h)": round(self.max_speed * 3.6, 2),
            "平均停车误差 (m)": (round(avg_stop_error, 2)
                                if avg_stop_error is not None else None),
            "最大停车误差 (m)": (round(max(self.stop_errors), 2)
                                if self.stop_errors else None),
            "超速次数": summary["超速次数"],
            "红灯违规次数": summary["红灯违规次数"],
            "紧急制动次数": summary["紧急制动次数"],
            "碰撞次数": summary["碰撞次数"],
            "安全事件总数": safety_events,
            "严重事件总数": summary["严重事件数"],
        }

    @classmethod
    def smoothness_metrics(cls, frames: list[dict]) -> tuple[Optional[float], dict]:
        """用真实时间下的加速度和冲动率趋势计算平稳性。"""
        accelerations: list[float] = []
        jerks: list[float] = []
        previous_acceleration: dict[str, tuple[float, float]] = {}
        previous_sign: dict[str, int] = {}
        switches = 0
        valid_duration = 0.0

        for previous, current in zip(frames, frames[1:]):
            dt = current["simTime"] - previous["simTime"]
            if not 0.05 <= dt <= 2.0:
                previous_acceleration.clear()
                continue
            previous_by_id = {item["id"]: item for item in previous["trains"]}
            for train in current["trains"]:
                train_id = train["id"]
                old = previous_by_id.get(train_id)
                if old is None or old["status"] != train["status"]:
                    previous_acceleration.pop(train_id, None)
                    continue

                old_speed = old["speedKmh"] / 3.6
                speed = train["speedKmh"] / 3.6
                travelled = abs(train["position"] - old["position"])
                if travelled > max(old_speed, speed) * dt + 30.0:
                    previous_acceleration.pop(train_id, None)
                    continue

                acceleration = (speed - old_speed) / dt
                accelerations.append(abs(acceleration))
                midpoint = (previous["simTime"] + current["simTime"]) / 2.0
                prior = previous_acceleration.get(train_id)
                if prior is not None:
                    jerk_dt = midpoint - prior[1]
                    if 0.05 <= jerk_dt <= 2.5:
                        jerks.append(abs(acceleration - prior[0]) / jerk_dt)
                previous_acceleration[train_id] = (acceleration, midpoint)

                sign = 1 if acceleration > 0.2 else -1 if acceleration < -0.2 else 0
                if sign:
                    if train_id in previous_sign and previous_sign[train_id] != sign:
                        switches += 1
                    previous_sign[train_id] = sign
                valid_duration += dt

        basis = {
            "accelerationP95": round(cls._percentile(accelerations, 0.95), 3),
            "jerkP95": round(cls._percentile(jerks, 0.95), 3),
            "tractionBrakeSwitchesPerMinute": round(
                switches / (valid_duration / 60.0) if valid_duration else 0.0, 3),
            "smoothnessSampleCount": len(accelerations),
        }
        if len(accelerations) < 5 or len(jerks) < 3:
            return None, basis

        jerk_score = max(0.0, 100.0 - max(0.0, basis["jerkP95"] - 0.5) * 35.0)
        acceleration_score = max(
            0.0, 100.0 - max(0.0, basis["accelerationP95"] - 1.0) * 30.0)
        switch_score = max(
            0.0,
            100.0 - max(0.0, basis["tractionBrakeSwitchesPerMinute"] - 4.0) * 10.0,
        )
        score = jerk_score * 0.7 + acceleration_score * 0.2 + switch_score * 0.1
        return round(score, 1), basis

    @classmethod
    def _weighted_score(cls, dimensions: dict) -> float:
        available = [
            (key, value) for key, value in dimensions.items()
            if value is not None
        ]
        weight_total = sum(cls.WEIGHTS[key] for key, _ in available)
        if not weight_total:
            return 0.0
        score = sum(value * cls.WEIGHTS[key] for key, value in available)
        return round(score / weight_total, 1)

    @staticmethod
    def _percentile(values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        position = (len(ordered) - 1) * ratio
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    @staticmethod
    def _round_optional(value: Optional[float]) -> Optional[float]:
        return round(value, 1) if value is not None else None

    @staticmethod
    def _grade(score: float) -> str:
        return ("优秀" if score >= 90 else "良好" if score >= 80
                else "合格" if score >= 60 else "不合格")

    def reset(self):
        """重置评价器。"""
        self.max_speed = 0.0
        self.stop_errors.clear()
