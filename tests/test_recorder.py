"""运行日志记录器测试"""

import csv
import os
import tempfile

from src.logger.recorder import Recorder
from src.logger.evaluator import Evaluator


def test_recorder_writes_csv_log_file():
    """记录事件时应同步写入 CSV 文件"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.start()
        recorder.record("测试", "写入日志", position=12.3, speed=4.5)
        recorder.close()

        assert recorder.log_path is not None
        assert os.path.exists(recorder.log_path)
        with open(recorder.log_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))

        assert rows[0] == [
            "timestamp", "event_type", "severity", "source", "train_id",
            "entity_id", "description", "position_m", "speed_m_s",
        ]
        assert rows[1][1:] == [
            "测试", "INFO", "system", "", "", "写入日志", "12.300", "4.500",
        ]


def test_recorder_ignores_events_before_start():
    """未开始运行前不创建日志文件，也不记录初始化事件"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.record("系统", "启动前事件")
        recorder.step(10.0)

        assert recorder.events == []
        assert recorder.log_path is None
        assert os.listdir(tmp_dir) == []


def test_structured_events_support_query_and_severity():
    """多列车日志应可按车号、来源和等级筛选。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.start()
        recorder.record(
            "超速", "2车超速", 120.0, 20.0,
            train_id="2车", source="protection", entity_id="LK12",
        )

        events = recorder.query(
            train_id="2车", source="protection", severity="warning")
        assert len(events) == 1
        assert events[0].entity_id == "LK12"
        assert recorder.get_summary()["警告事件数"] == 1
        recorder.close()


def test_close_allows_safe_restart_without_overwriting_log():
    """关闭后重启应创建新批次文件。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.start()
        first_path = recorder.log_path
        recorder.close()
        assert recorder.is_active is False

        recorder.start()
        second_path = recorder.log_path
        recorder.close()
        assert first_path != second_path
        assert len(os.listdir(tmp_dir)) == 2


def test_evaluator_uses_incident_counts_and_returns_grade():
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.start()
        recorder.record("超速", "一次超速")
        recorder.record("紧急制动", "一次紧急制动")
        evaluator = Evaluator()
        evaluator.update_max_speed(20.0)

        result = evaluator.evaluate(recorder)
        assert result["综合得分"] == 85.0
        assert result["评价等级"] == "良好"
        assert result["安全事件总数"] == 2
        recorder.close()
