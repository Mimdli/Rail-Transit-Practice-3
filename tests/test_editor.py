"""TrackEditor 单元测试"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.track.editor import TrackEditor
from src.track.db_loader import DBLoader


# 使用真实数据库测试（只读操作）
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
HAS_DB = os.path.exists(DB_PATH)


def test_editor_list_stations():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    with TrackEditor() as editor:
        stations = editor.list_stations()
        assert len(stations) >= 10, f"车站数量不足: {len(stations)}"
        # 检查字段
        for s in stations:
            assert "station_id" in s
            assert "name" in s


def test_editor_list_segments():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    with TrackEditor() as editor:
        segs = editor.list_segments()
        assert len(segs) > 100, f"Seg 数量不足: {len(segs)}"
        for s in segs:
            assert s["length"] > 0


def test_editor_list_speed_limits():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    with TrackEditor() as editor:
        limits = editor.list_speed_limits()
        assert len(limits) > 100
        # 按区段过滤
        seg_limits = editor.list_speed_limits(seg_id=1)
        assert len(seg_limits) > 0


def test_editor_list_gradients():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    with TrackEditor() as editor:
        grads = editor.list_gradients()
        assert len(grads) > 100
        seg_grads = editor.list_gradients(seg_id=1)
        assert len(seg_grads) > 0


def test_editor_get_single():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    with TrackEditor() as editor:
        station = editor.get_station(1)
        assert station is not None
        assert station["name"]


def test_editor_add_and_delete_station():
    """在临时数据库中测试增删"""
    # 创建临时数据库副本
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    tmp_dir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmp_dir, "test.db")
    shutil.copy2(DB_PATH, tmp_db)

    try:
        with TrackEditor(tmp_db) as editor:
            # 新增
            editor.add_station(999, "测试站A", 12345.0)
            station = editor.get_station(999)
            assert station is not None
            assert station["name"] == "测试站A"
            assert station["position"] == 12345.0

            # 更新
            editor.update_station(999, name="测试站B")
            station = editor.get_station(999)
            assert station["name"] == "测试站B"

            # 删除
            editor.delete_station(999)
            station = editor.get_station(999)
            assert station is None
    finally:
        shutil.rmtree(tmp_dir)


def test_editor_add_and_delete_speed_limit():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    tmp_dir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmp_dir, "test.db")
    shutil.copy2(DB_PATH, tmp_db)

    try:
        with TrackEditor(tmp_db) as editor:
            # 新增限速
            new_id = editor.add_speed_limit(1, 0.0, 100.0, 15.0)
            assert new_id > 0

            sl = editor.get_speed_limit(new_id)
            assert sl is not None
            assert sl["speed_limit"] == 15.0

            # 批量修改 Seg 1 的限速
            editor.update_speed_limit_on_seg(1, 20.0)
            seg_limits = editor.list_speed_limits(seg_id=1)
            for sl in seg_limits:
                assert sl["speed_limit"] == 20.0

            # 清理测试数据
            editor.clear_speed_limits_on_seg(1)
            seg_limits = editor.list_speed_limits(seg_id=1)
            assert len(seg_limits) == 0
    finally:
        shutil.rmtree(tmp_dir)


def test_editor_copy_segment():
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    tmp_dir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmp_dir, "test.db")
    shutil.copy2(DB_PATH, tmp_db)

    try:
        with TrackEditor(tmp_db) as editor:
            result = editor.copy_segment(1, 9999)
            assert result == 1
            seg = editor.get_segment(9999)
            assert seg is not None
            assert seg["length"] > 0
            limits = editor.list_speed_limits(seg_id=9999)
            assert len(limits) > 0
            grads = editor.list_gradients(seg_id=9999)
            assert len(grads) > 0

            # 先删子记录再删 segment
            for sl in limits:
                editor.delete_speed_limit(sl["limit_id"])
            for g in grads:
                editor.delete_gradient(g["gradient_id"])
            editor.delete_segment(9999)
            assert editor.get_segment(9999) is None
    finally:
        shutil.rmtree(tmp_dir)


def test_editor_load_after_edit():
    """编辑后加载到 TrackData 应包含所有数据"""
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return

    with TrackEditor() as editor:
        td = editor.load_to_track_data()
        assert len(td.segments) > 100
        assert len(td.stations) >= 10
        assert td.total_length() > 10000


def test_update_station_using_context_manager():
    """验证 context manager 自动提交"""
    if not HAS_DB:
        print("SKIP: 数据库不存在")
        return
    tmp_dir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmp_dir, "test.db")
    shutil.copy2(DB_PATH, tmp_db)

    try:
        with TrackEditor(tmp_db) as editor:
            editor.add_station(998, "临时站", 5000.0)

        # 确认提交成功
        with TrackEditor(tmp_db) as editor:
            s = editor.get_station(998)
            assert s is not None
            editor.delete_station(998)
    finally:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    tests = [
        ("list_stations", test_editor_list_stations),
        ("list_segments", test_editor_list_segments),
        ("list_speed_limits", test_editor_list_speed_limits),
        ("list_gradients", test_editor_list_gradients),
        ("get_single", test_editor_get_single),
        ("add_and_delete_station", test_editor_add_and_delete_station),
        ("add_and_delete_speed_limit", test_editor_add_and_delete_speed_limit),
        ("copy_segment", test_editor_copy_segment),
        ("load_after_edit", test_editor_load_after_edit),
        ("context_manager", test_update_station_using_context_manager),
    ]

    passed = 0
    failed = 0
    for name, func in tests:
        try:
            func()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"  总计: {passed + failed}, 通过: {passed}, 失败: {failed}")
    print(f"{'='*40}")
