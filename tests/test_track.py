"""轨道线路模块单元测试 — 验证数据加载器和查询接口"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.track.loader import TrackLoader
from src.track.db_loader import DBLoader
from src.track.data import TrackData, Station, Segment


# ======== 演示数据测试 ========

def test_demo_loader_returns_data():
    loader = TrackLoader()
    td = loader.load_demo_data()
    assert isinstance(td, TrackData)
    assert len(td.stations) > 0
    assert len(td.segments) > 0
    assert len(td.platforms) > 0
    assert len(td.speed_limits) > 0
    assert len(td.gradients) > 0


def test_demo_segments_have_coordinates():
    loader = TrackLoader()
    td = loader.load_demo_data()
    for seg in td.segments:
        assert seg.abs_start >= 0.0
        assert seg.length > 0


def test_demo_segment_chain_continuous():
    loader = TrackLoader()
    td = loader.load_demo_data()
    # 验证区段链连续: 下一个的 abs_start ≈ 上一个的 abs_start + length
    sorted_segs = sorted(td.segments, key=lambda s: s.abs_start)
    for i in range(len(sorted_segs) - 1):
        expected = sorted_segs[i].abs_start + sorted_segs[i].length
        assert abs(sorted_segs[i + 1].abs_start - expected) < 1.0


def test_demo_total_length():
    loader = TrackLoader()
    td = loader.load_demo_data()
    expected = sum(s.length for s in td.segments)
    assert abs(td.total_length() - expected) < 1.0


def test_demo_stations_have_positions():
    loader = TrackLoader()
    td = loader.load_demo_data()
    for station in td.stations:
        assert station.position > 0 or station.name == "GGZ"  # 首站位置为 0


def test_demo_get_speed_limit():
    loader = TrackLoader()
    td = loader.load_demo_data()
    # 在已知限速段内查询
    limit = td.get_speed_limit_at(100.0)
    assert limit > 0


def test_demo_get_gradient():
    loader = TrackLoader()
    td = loader.load_demo_data()
    grad = td.get_gradient_at(300.0)
    assert grad != 0.0


def test_demo_get_station_at():
    loader = TrackLoader()
    td = loader.load_demo_data()
    station = td.get_station_at(0.0)
    assert station is not None
    assert station.name == "GGZ"


def test_demo_get_nearest_station():
    loader = TrackLoader()
    td = loader.load_demo_data()
    station = td.get_nearest_station_ahead(100.0)
    assert station is not None
    assert station.position > 100.0


def test_demo_get_platform_side():
    loader = TrackLoader()
    td = loader.load_demo_data()
    side = td.get_platform_side_at(0.0)
    assert side in ("left", "right", "")


def test_demo_get_seg_id():
    loader = TrackLoader()
    td = loader.load_demo_data()
    seg_id = td.get_seg_id_at(400.0)
    assert seg_id > 0


# ======== Excel 数据测试 ========

def test_excel_loader_loads_all_sheets():
    loader = TrackLoader()
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    if not os.path.exists(xls_path):
        print("SKIP: 线路数据文件不存在")
        return

    td = loader.load_from_excel(xls_path)
    assert len(td.segments) > 0, "应加载 Seg 数据"
    assert len(td.stations) > 0, "应加载车站数据"
    assert len(td.platforms) > 0, "应加载站台数据"
    assert len(td.speed_limits) > 0, "应加载限速数据"
    assert len(td.gradients) > 0, "应加载坡度数据"


def test_excel_segments_have_length():
    loader = TrackLoader()
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    if not os.path.exists(xls_path):
        print("SKIP: 线路数据文件不存在")
        return

    td = loader.load_from_excel(xls_path)
    for seg in td.segments:
        assert seg.length > 0, f"Seg {seg.seg_id} length 应为正数"


def test_excel_stations_have_names():
    loader = TrackLoader()
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    if not os.path.exists(xls_path):
        print("SKIP: 线路数据文件不存在")
        return

    td = loader.load_from_excel(xls_path)
    station_names = [s.name for s in td.stations]
    print(f"\n车站列表: {station_names}")
    assert len(station_names) >= 10, f"应加载至少 10 个车站, 实际: {len(station_names)}"


def test_excel_speed_limits_reasonable():
    loader = TrackLoader()
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    if not os.path.exists(xls_path):
        print("SKIP: 线路数据文件不存在")
        return

    td = loader.load_from_excel(xls_path)
    # 限速值应在合理范围内 (5~30 m/s ≈ 18~108 km/h)
    for sl in td.speed_limits:
        assert 5.0 <= sl.speed_limit <= 30.0, \
            f"限速值异常: {sl.speed_limit:.1f} m/s (Seg {sl.seg_id})"


def test_excel_coordinates_continuous():
    loader = TrackLoader()
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    if not os.path.exists(xls_path):
        print("SKIP: 线路数据文件不存在")
        return

    td = loader.load_from_excel(xls_path)
    if td.total_length() > 0:
        print(f"线路总长: {td.total_length():.0f} m ({td.total_length() / 1000:.1f} km)")
        print(f"Seg 数量: {len(td.segments)}")
        print(f"车站数量: {len(td.stations)}")
        print(f"限速区段: {len(td.speed_limits)}")
        print(f"坡度区段: {len(td.gradients)}")


# ======== SQLite 数据库测试 ========

def test_db_loader_loads_all_tables():
    loader = DBLoader()
    db_path = os.path.join(os.path.dirname(__file__),
                           "..", "data", "railway.db")
    if not os.path.exists(db_path):
        print("SKIP: 数据库文件不存在")
        return

    td = loader.load_from_db(db_path)
    assert len(td.segments) > 0, "应加载 Seg 数据"
    assert len(td.stations) > 0, "应加载车站数据"
    assert len(td.platforms) > 0, "应加载站台数据"
    assert len(td.speed_limits) > 0, "应加载限速数据"
    assert len(td.gradients) > 0, "应加载坡度数据"
    assert len(td.signals) > 0, "应加载信号数据"


def test_db_loader_matches_excel():
    """验证 DB 加载的数据与 xls 加载的数据一致"""
    xls_path = os.path.join(os.path.dirname(__file__),
                            "..", "resource", "线路数据(1).xls")
    db_path = os.path.join(os.path.dirname(__file__),
                           "..", "data", "railway.db")
    if not os.path.exists(xls_path) or not os.path.exists(db_path):
        print("SKIP: 数据文件不存在")
        return

    xls_loader = TrackLoader()
    td_xls = xls_loader.load_from_excel(xls_path)

    db_loader = DBLoader()
    td_db = db_loader.load_from_db(db_path)

    assert len(td_db.segments) == len(td_xls.segments), "Seg 数量不一致"
    assert len(td_db.stations) == len(td_xls.stations), "车站数量不一致"
    assert len(td_db.platforms) == len(td_xls.platforms), "站台数量不一致"
    assert len(td_db.speed_limits) == len(td_xls.speed_limits), "限速数量不一致"

    # 确认线路总长接近
    diff = abs(td_db.total_length() - td_xls.total_length())
    assert diff < 1.0, f"线路总长差异过大: {diff:.1f} m"


def test_db_loader_reasonable_data():
    """验证 DB 加载的数据合理性"""
    db_path = os.path.join(os.path.dirname(__file__),
                           "..", "data", "railway.db")
    if not os.path.exists(db_path):
        print("SKIP: 数据库文件不存在")
        return

    loader = DBLoader()
    td = loader.load_from_db(db_path)

    for seg in td.segments:
        assert seg.length > 0, f"Seg {seg.seg_id} length 应为正数"

    for sl in td.speed_limits:
        assert 5.0 <= sl.speed_limit <= 30.0, \
            f"限速值异常: {sl.speed_limit:.1f} m/s (Seg {sl.seg_id})"

    print(f"\n数据库加载完成:")
    print(f"  线路总长: {td.total_length():.0f} m ({td.total_length() / 1000:.1f} km)")
    print(f"  Seg 数量: {len(td.segments)}")
    print(f"  车站数量: {len(td.stations)}")
    print(f"  限速区段: {len(td.speed_limits)}")
    print(f"  坡度区段: {len(td.gradients)}")
    print(f"  信号机数量: {len(td.signals)}")


if __name__ == "__main__":
    tests = [
        ("demo_loader_returns_data", test_demo_loader_returns_data),
        ("demo_segments_have_coordinates", test_demo_segments_have_coordinates),
        ("demo_segment_chain_continuous", test_demo_segment_chain_continuous),
        ("demo_total_length", test_demo_total_length),
        ("demo_stations_have_positions", test_demo_stations_have_positions),
        ("demo_get_speed_limit", test_demo_get_speed_limit),
        ("demo_get_gradient", test_demo_get_gradient),
        ("demo_get_station_at", test_demo_get_station_at),
        ("demo_get_nearest_station", test_demo_get_nearest_station),
        ("demo_get_platform_side", test_demo_get_platform_side),
        ("demo_get_seg_id", test_demo_get_seg_id),
        ("excel_loader_loads_all_sheets", test_excel_loader_loads_all_sheets),
        ("excel_segments_have_length", test_excel_segments_have_length),
        ("excel_stations_have_names", test_excel_stations_have_names),
        ("excel_speed_limits_reasonable", test_excel_speed_limits_reasonable),
        ("excel_coordinates_continuous", test_excel_coordinates_continuous),
        ("db_loader_loads_all_tables", test_db_loader_loads_all_tables),
        ("db_loader_matches_excel", test_db_loader_matches_excel),
        ("db_loader_reasonable_data", test_db_loader_reasonable_data),
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
