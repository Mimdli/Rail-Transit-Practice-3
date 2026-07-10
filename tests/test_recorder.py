"""运行日志记录器测试"""

import csv
import os
import tempfile

from src.logger.recorder import Recorder


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

        assert rows[0] == ["timestamp", "event_type", "description", "position_m", "speed_m_s"]
        assert rows[1][1:] == ["测试", "写入日志", "12.300", "4.500"]


def test_recorder_ignores_events_before_start():
    """未开始运行前不创建日志文件，也不记录初始化事件"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = Recorder(log_dir=tmp_dir)
        recorder.record("系统", "启动前事件")
        recorder.step(10.0)

        assert recorder.events == []
        assert recorder.log_path is None
        assert os.listdir(tmp_dir) == []
