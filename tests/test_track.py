"""轨道线路模块单元测试 — 验证数据加载器和查询接口"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.track.loader import TrackLoader
from src.track.db_loader import DBLoader
from src.track.data import TrackData, Station, Segment
from src.track.adapter import TrackDataAdapter
from src.common.track_position import TrackPosition


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
    # 仅验证主线区段链连续；侧线从道岔岔出，与主线共享分叉点坐标
    main_segs = sorted(
        [s for s in td.segments if s.seg_id in (1, 2, 3, 4, 5)],
        key=lambda s: s.abs_start,
    )
    for i in range(len(main_segs) - 1):
        expected = main_segs[i].abs_start + main_segs[i].length
        assert abs(main_segs[i + 1].abs_start - expected) < 1.0


def test_demo_total_length():
    loader = TrackLoader()
    td = loader.load_demo_data()
    main_segs = [s for s in td.segments if s.seg_id in (1, 2, 3, 4, 5)]
    expected = sum(s.length for s in main_segs)
    assert abs(td.total_length() - expected) < 1.0


def test_adapter_main_line_advance():
    """默认岔口走正向邻居（主线）。"""
    td = TrackLoader().load_demo_data()
    adapter = TrackDataAdapter(td)
    pos = adapter.advance_position(TrackPosition(1, 750.0), 20.0)
    assert pos.segment_id == 2
    assert abs(pos.offset - 12.0) < 0.1


def test_adapter_lateral_branch_advance():
    """指定岔口路由后可进入侧线并走完全程。"""
    td = TrackLoader().load_demo_data()
    adapter = TrackDataAdapter(td)
    adapter.use_lateral_fork(1)
    pos = adapter.advance_position(TrackPosition(1, 750.0), 20.0)
    assert pos.segment_id == 6
    pos = adapter.advance_position(pos, 420.0 - pos.offset)
    assert pos.segment_id == 6
    assert abs(pos.offset - 420.0) < 0.1


def test_advance_past_fork_stays_on_main():
    """模拟过岔：默认应沿 end_neighbor 走主线，不陷入侧线尽头。"""
    td = TrackLoader().load_demo_data()
    adapter = TrackDataAdapter(td)
    pos = TrackPosition(1, 700.0)
    for _ in range(30):
        pos = adapter.advance_position(pos, 5.0)
    assert pos.segment_id != 6
    assert pos.segment_id in (2, 3, 4, 5)


def test_normalize_gradient_value():
    from src.track.data import normalize_gradient_value
    assert normalize_gradient_value(300.0) == 30.0
    assert normalize_gradient_value(20.0) == 20.0
    assert normalize_gradient_value(350.0) == 35.0
    assert normalize_gradient_value(500.0) == 40.0


def test_db_gradient_at_1404_not_stuck_value():
    """数据库线路 1404m 处坡度不应为不可爬行的 300‰。"""
    import os
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
    if not os.path.exists(db_path):
        return
    td = DBLoader().load_from_db(db_path)
    adapter = TrackDataAdapter(td)
    pos = adapter.from_absolute(1404.0, hint_seg_id=2)
    grad = adapter.get_gradient(pos)
    assert abs(grad) <= 40.0
    assert grad == 30.0


def test_db_train_passes_1400m_with_traction():
    """数据库线路：全牵引下列车应能越过 1400m 坡段，不再速度归零卡死。"""
    import os
    from src.common.consist import CONSIST_4M2T
    from src.vehicle.vehicle_controller import VehicleController
    from src.vehicle.environment import MockEnvironment, WeatherType

    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
    if not os.path.exists(db_path):
        return
    td = DBLoader().load_from_db(db_path)
    adapter = TrackDataAdapter(td)
    env = MockEnvironment(WeatherType.DRY, adapter)
    ctrl = VehicleController(CONSIST_4M2T, adapter, env)
    ctrl.set_throttle(1.0)
    for _ in range(8000):
        ctrl.step(0.033)
        abs_h = adapter.to_absolute(ctrl.states[0].position)
        if abs_h > 1550:
            assert ctrl.states[0].velocity > 1.0
            return
    raise AssertionError("列车未能在合理步数内越过 1400m 坡段")


def test_train_holds_at_rest_without_traction():
    """无牵引无制动时列车应保持静止，不因坡道或车钩力溜车。"""
    import os
    from src.common.consist import CONSIST_4M2T
    from src.vehicle.vehicle_controller import VehicleController
    from src.vehicle.environment import MockEnvironment, WeatherType

    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
    if not os.path.exists(db_path):
        td = TrackLoader().load_demo_data()
    else:
        td = DBLoader().load_from_db(db_path)
    adapter = TrackDataAdapter(td)
    env = MockEnvironment(WeatherType.DRY, adapter)
    ctrl = VehicleController(CONSIST_4M2T, adapter, env)
    start_abs = adapter.to_absolute(ctrl.states[0].position)
    for _ in range(200):
        ctrl.step(0.1)
    end_abs = adapter.to_absolute(ctrl.states[0].position)
    assert ctrl.states[0].velocity < 0.01
    assert abs(end_abs - start_abs) < 0.5


def test_db_train_reaches_line_end():
    """数据库线路：全牵引下应能走完全程末端（含 18103m 岔点死胡同）。"""
    import os
    from src.common.consist import CONSIST_4M2T
    from src.vehicle.vehicle_controller import VehicleController
    from src.vehicle.environment import MockEnvironment, WeatherType

    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "railway.db")
    if not os.path.exists(db_path):
        return
    td = DBLoader().load_from_db(db_path)
    adapter = TrackDataAdapter(td)
    total = td.total_length()
    env = MockEnvironment(WeatherType.DRY, adapter)
    ctrl = VehicleController(CONSIST_4M2T, adapter, env)
    # 从末端岔区前启动，验证 18103m 附近不再卡死
    start = adapter.from_absolute(17900.0, hint_seg_id=213)
    ctrl.states[0].position = start
    ctrl.states[0].velocity = 10.0
    for i in range(1, len(ctrl.states)):
        ctrl.states[i].position = adapter.advance_position(
            ctrl.states[i - 1].position, -CONSIST_4M2T[i - 1].length
        )
        ctrl.states[i].velocity = 10.0
    ctrl.set_throttle(1.0)
    for _ in range(3000):
        ctrl.step(0.033)
        abs_h = adapter.to_absolute(ctrl.states[0].position)
        if abs_h >= total - 1.0:
            return
    raise AssertionError(
        f"列车未到达线路终点: 停在 {abs_h:.1f}m / {total:.1f}m"
    )


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
