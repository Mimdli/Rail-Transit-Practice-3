"""轨道交通模拟系统 — Web 接口

基于 Flask 的 Web 界面，导入原始 src/ 模块（不修改任何源代码），
通过 HTTP API 和 SSE (Server-Sent Events) 提供实时交互。

启动方式:
    cd Rail-Transit-Practice-3
    python web/app.py
    浏览器访问 http://localhost:5000
"""

import sys
import os
import time
import json
import threading

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, request, render_template, Response, stream_with_context
from flask_socketio import SocketIO, emit

# 导入原始模块（不修改源代码）
from src.vehicle.model import VehicleModel, RunningMode, ControlLevel, DoorSide
from src.vehicle.controller import ManualController, AutoController
from src.track.loader import TrackLoader
from src.track.data import TrackData
from src.signal.system import SignalSystem, SignalAspect
from src.power.supply import PowerSupply, PowerStatus
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder
from src.logger.evaluator import Evaluator

# ---------------------------------------------------------------------------
# Flask 应用
# ---------------------------------------------------------------------------

app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
            static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = 'rail-transit-sim'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ---------------------------------------------------------------------------
# 全局仿真状态（线程安全）
# ---------------------------------------------------------------------------

class SimulationEngine:
    """仿真引擎 — 封装全部核心模块，与 MainWindow._init_modules() 完全对应。"""

    def __init__(self):
        self.vehicle = VehicleModel()
        self.manual_ctrl = ManualController(self.vehicle)
        self.auto_ctrl = AutoController(self.vehicle)

        # 尝试加载真实 Excel 数据，失败则用演示数据
        self.data_mode = "demo"
        self.track = self._load_track_data()

        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.interlock = DoorInterlock(self.vehicle, self.track)
        self.recorder = Recorder()
        self.evaluator = Evaluator()

        self.front_train_positions: list = [300.0]
        self._last_signal_aspects: dict = {}
        self._last_status_log_time: float = -1.0
        self.sim_time: float = 0.0
        self.running: bool = False
        self._lock = threading.Lock()

        self.recorder.record("系统", "系统启动，当前数据源: 演示数据" if self.data_mode == "demo" else "系统启动，数据源: Excel 线路数据")

    def _load_track_data(self) -> TrackData:
        """加载线路数据：优先使用 Excel 文件，失败时使用演示数据。"""
        excel_paths = [
            os.path.join(PROJECT_ROOT, 'resource', '线路数据(1).xls'),
            os.path.join(PROJECT_ROOT, '..', '线路数据(1)(1).xls'),
        ]
        for path in excel_paths:
            if os.path.exists(path):
                try:
                    loader = TrackLoader()
                    self.data_mode = "excel"
                    return loader.load_from_excel(path)
                except Exception as e:
                    print(f"[WARN] Excel 加载失败 ({path}): {e}")
        print("[INFO] 使用演示线路数据")
        return TrackLoader().load_demo_data()

    def get_track_geometry(self) -> dict:
        """获取线路几何数据，用于前端 SVG 线路图渲染。"""
        td = self.track

        # 计算分支层级（BFS，与 TrackView._compute_branch_levels 一致）
        branch_levels = {}
        if td.segments and td._seg_map:
            from collections import deque
            referenced = set()
            for s in td.segments:
                for n in (s.start_neighbor, s.end_neighbor):
                    if n > 0 and n != 65535:
                        referenced.add(n)
            root_id = None
            for s in td.segments:
                if s.seg_id not in referenced:
                    root_id = s.seg_id
                    break
            if root_id is None:
                root_id = td.segments[0].seg_id

            visited = set()
            q = deque([root_id])
            visited.add(root_id)
            branch_levels[root_id] = 0
            while q:
                sid = q.popleft()
                seg = td._seg_map.get(sid)
                if not seg:
                    continue
                cur_level = branch_levels.get(sid, 0)
                for nid in (seg.start_neighbor, seg.end_neighbor):
                    if nid <= 0 or nid == 65535 or nid not in td._seg_map:
                        continue
                    if nid not in visited:
                        visited.add(nid)
                        branch_levels[nid] = cur_level
                        q.append(nid)
                for nid in (seg.start_lateral, seg.end_lateral):
                    if nid <= 0 or nid == 65535 or nid not in td._seg_map:
                        continue
                    if nid not in visited:
                        visited.add(nid)
                        branch_levels[nid] = cur_level + 1
                        q.append(nid)
                    else:
                        branch_levels[nid] = min(
                            branch_levels.get(nid, 999), cur_level + 1)

        # 区段
        segments = []
        for s in td.segments:
            segments.append({
                "seg_id": s.seg_id,
                "abs_start": s.abs_start,
                "length": s.length,
                "level": branch_levels.get(s.seg_id, 0),
            })

        # 车站
        stations = []
        for s in td.stations:
            stations.append({
                "station_id": s.station_id,
                "name": s.name,
                "position": s.position,
                "platform_ids": s.platform_ids,
            })

        # 限速区段
        speed_limits = []
        for sl in td.speed_limits:
            speed_limits.append({
                "abs_start": sl.abs_start,
                "abs_end": sl.abs_end,
                "speed_kmh": round(sl.speed_limit * 3.6, 1),
            })

        # 坡度区段
        gradients = []
        for g in td.gradients:
            gradients.append({
                "abs_start": g.abs_start,
                "abs_end": g.abs_end,
                "gradient": g.gradient,
            })

        # 信号机
        signals_out = []
        for sig in td.signals:
            signals_out.append({
                "signal_id": sig.signal_id,
                "position": sig.position,
                "direction": sig.direction,
            })

        # 信号当前状态
        signal_aspects = {}
        for sig in td.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            signal_aspects[sig.signal_id] = aspect.value

        # 道岔（侧向连接点）
        switches = []
        for s in td.segments:
            seg_level = branch_levels.get(s.seg_id, 0)
            if s.start_lateral > 0 and s.start_lateral != 65535:
                lateral_seg = td._seg_map.get(s.start_lateral)
                if lateral_seg:
                    lat_level = branch_levels.get(s.start_lateral, 0)
                    switches.append({
                        "position": s.abs_start,
                        "from_seg": s.seg_id,
                        "to_seg": s.start_lateral,
                        "from_level": seg_level,
                        "to_level": lat_level,
                        "direction": "left" if lat_level > seg_level else "right",
                    })
            if s.end_lateral > 0 and s.end_lateral != 65535:
                lateral_seg = td._seg_map.get(s.end_lateral)
                if lateral_seg:
                    lat_level = branch_levels.get(s.end_lateral, 0)
                    switches.append({
                        "position": s.abs_start + s.length,
                        "from_seg": s.seg_id,
                        "to_seg": s.end_lateral,
                        "from_level": seg_level,
                        "to_level": lat_level,
                        "direction": "left" if lat_level > seg_level else "right",
                    })

        # 去重：同一位置可能有双向道岔
        seen_pos = set()
        unique_switches = []
        for sw in switches:
            key = f"{sw['position']:.2f}_{sw['from_seg']}_{sw['to_seg']}"
            if key not in seen_pos:
                seen_pos.add(key)
                unique_switches.append(sw)
        unique_switches.sort(key=lambda x: x["position"])

        return {
            "total_length": td.total_length(),
            "segments": segments,
            "stations": stations,
            "speed_limits": speed_limits,
            "gradients": gradients,
            "signals": signals_out,
            "signal_aspects": signal_aspects,
            "switches": unique_switches,
        }

    def step(self):
        """推进一个仿真步（与 MainWindow._update() 逻辑一致）"""
        with self._lock:
            dt = self.vehicle.dt
            pos = self.vehicle.position

            # 更新线路条件
            self.vehicle.current_gradient = self.track.get_gradient_at(pos)
            track_limit = self.track.get_speed_limit_at(pos)

            # 信号
            self.signal_system.update_aspects_by_occupancy(
                self.track.signals, self.front_train_positions
            )
            self._record_signal_changes()
            self.vehicle.current_speed_limit = self.signal_system.get_effective_speed_limit(
                pos, track_limit, self.track.signals
            )

            # 供电影响
            if not self.power_supply.can_traction():
                if self.vehicle.control_level.value > 0:
                    self.vehicle.set_control_level_direct(self.vehicle.control_level)

            # 自动驾驶
            if self.vehicle.running_mode == RunningMode.AUTOMATIC:
                self.auto_ctrl.step()

            # 推进仿真
            self.vehicle.step()
            self.power_supply.step(dt)
            self.recorder.step(dt)
            self.sim_time += dt

            # 评价
            self.evaluator.update_max_speed(self.vehicle.speed)
            self._record_status_snapshot()

            # 超速检测
            if self.vehicle.speed > self.vehicle.current_speed_limit + 0.5:
                self.recorder.record("超速", f"超速: {self.vehicle.get_speed_kmh():.1f} km/h", pos, self.vehicle.speed)

    def _record_signal_changes(self):
        for sig in self.track.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            last_aspect = self._last_signal_aspects.get(sig.signal_id)
            if aspect != last_aspect:
                self.recorder.record("信号", f"{sig.signal_id} {aspect.value}", sig.position, self.vehicle.speed)
                self._last_signal_aspects[sig.signal_id] = aspect

    def _record_status_snapshot(self):
        if self._last_status_log_time >= 0 and self.sim_time - self._last_status_log_time < 1.0:
            return
        nearest_signal = self.signal_system.get_nearest_signal_ahead(self.vehicle.position, self.track.signals)
        if nearest_signal:
            aspect = self.signal_system.get_signal_aspect(nearest_signal)
            signal_text = f"{nearest_signal.signal_id} {aspect.value}"
        else:
            signal_text = "无前方信号"
        mode = "自动" if self.vehicle.running_mode == RunningMode.AUTOMATIC else "手动"
        description = (
            f"状态快照: 模式 {mode} · 加速度 {self.vehicle.acceleration:+.2f} m/s² · "
            f"限速 {self.vehicle.current_speed_limit * 3.6:.0f} km/h · "
            f"信号 {signal_text} · 供电 {self.power_supply.status.value}"
        )
        self.recorder.record("状态", description, self.vehicle.position, self.vehicle.speed)
        self._last_status_log_time = self.sim_time

    # ---- 对外只读快照（线程安全）----------------------------------

    def get_state(self) -> dict:
        """获取当前仿真状态快照。"""
        with self._lock:
            v = self.vehicle
            pos = v.position

            # 信号
            next_sig = self.signal_system.get_nearest_signal_ahead(pos, self.track.signals)
            if next_sig:
                aspect = self.signal_system.get_signal_aspect(next_sig)
                sig_info = f"{next_sig.signal_id}: {aspect.value} ({next_sig.position - pos:.0f}m)"
            else:
                aspect = self.signal_system.get_aspect_at(pos, self.track.signals)
                sig_info = aspect.value

            # 车站
            station = self.track.get_station_at(pos)
            current_station = station.name if station else "区间"
            next_station = self.track.get_nearest_station_ahead(pos)
            next_station_name = next_station.name if next_station else "终点"
            next_station_distance = (next_station.position - pos) if next_station else 0

            # 车门
            if v.left_door_open:
                door_text = "左门开"
            elif v.right_door_open:
                door_text = "右门开"
            else:
                door_text = "全部关闭"

            # 线路进度
            total = self.track.total_length()
            progress = (pos / total * 100) if total > 0 else 0

            # 前方信号序列
            ahead_signals = [sig for sig in self.track.signals if sig.position >= pos][:5]
            signal_sequence = []
            for sig in ahead_signals:
                s_aspect = self.signal_system.get_signal_aspect(sig)
                signal_sequence.append({
                    "id": sig.signal_id,
                    "aspect": s_aspect.value,
                    "distance": sig.position - pos,
                    "color": {"绿灯": "#16a34a", "黄灯": "#d97706", "红灯": "#dc2626"}.get(s_aspect.value, "#666"),
                })

            # 信号当前状态映射
            signal_aspects_map = {}
            for sig in self.track.signals:
                s_aspect = self.signal_system.get_signal_aspect(sig)
                signal_aspects_map[sig.signal_id] = s_aspect.value

            return {
                "sim_time": self.sim_time,
                "position": pos,
                "speed": v.speed,
                "speed_kmh": v.get_speed_kmh(),
                "acceleration": v.acceleration,
                "speed_limit": v.current_speed_limit,
                "speed_limit_kmh": v.current_speed_limit * 3.6,
                "gradient": v.current_gradient,
                "mode": "自动" if v.running_mode == RunningMode.AUTOMATIC else "手动",
                "signal": sig_info,
                "signal_sequence": signal_sequence,
                "signal_aspects": signal_aspects_map,
                "power": self.power_supply.status.value,
                "door": door_text,
                "current_station": current_station,
                "next_station": next_station_name,
                "next_station_distance": next_station_distance,
                "progress": progress,
                "total_length": total,
                "stations": [{"name": s.name, "position": s.position} for s in self.track.stations],
                "evaluation": self.evaluator.evaluate(self.recorder),
            }

    def get_logs(self, since: int = 0) -> list:
        """获取 since 之后的事件列表。"""
        events = self.recorder.events[since:]
        return [{
            "timestamp": e.timestamp,
            "type": e.event_type,
            "description": e.description,
            "position": e.position,
            "speed": e.speed * 3.6,
        } for e in events]


# ---- 全局引擎实例 ----

engine = SimulationEngine()

# ---------------------------------------------------------------------------
# 仿真后台线程
# ---------------------------------------------------------------------------

def simulation_loop():
    """后台仿真循环，100ms 一步。"""
    while True:
        if engine.running:
            engine.step()
        time.sleep(0.1)

sim_thread = threading.Thread(target=simulation_loop, daemon=True)
sim_thread.start()

# ---------------------------------------------------------------------------
# 路由 — 页面
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')

# ---------------------------------------------------------------------------
# 路由 — API
# ---------------------------------------------------------------------------

@app.route('/api/state')
def api_state():
    """获取当前仿真状态"""
    return jsonify(engine.get_state())

@app.route('/api/logs')
def api_logs():
    """获取事件日志"""
    since = request.args.get('since', 0, type=int)
    return jsonify(engine.get_logs(since))

@app.route('/api/reset', methods=['POST'])
def api_reset():
    """重置仿真"""
    with engine._lock:
        engine.vehicle.reset()
        engine.sim_time = 0.0
        engine.recorder.clear()
        engine.evaluator.reset()
        engine.power_supply.reset()
        engine.signal_system.clear_signal_aspects()
        engine._last_signal_aspects.clear()
        engine._last_status_log_time = -1.0
        engine.recorder.record("系统", "仿真已重置")
    return jsonify({"status": "ok"})

# ---------------------------------------------------------------------------
# 路由 — 控制指令
# ---------------------------------------------------------------------------

@app.route('/api/control', methods=['POST'])
def api_control():
    """接收控制指令"""
    data = request.get_json()
    action = data.get('action', '')

    with engine._lock:
        vehicle = engine.vehicle
        manual = engine.manual_ctrl
        auto = engine.auto_ctrl
        interlock = engine.interlock
        recorder = engine.recorder
        pos = vehicle.position
        spd = vehicle.speed

        if action == 'traction':
            manual.set_traction()
            recorder.record("操作", "牵引", pos, spd)
        elif action == 'coast':
            manual.set_coast()
            recorder.record("操作", "惰行", pos, spd)
        elif action == 'service_brake':
            manual.set_service_brake()
            recorder.record("操作", "常用制动", pos, spd)
        elif action == 'emergency_brake':
            manual.set_emergency_brake()
            recorder.record("紧急制动", "按下紧急制动按钮", pos, spd)
        elif action == 'full_brake':
            manual.set_full_brake()
            recorder.record("操作", "全制动", pos, spd)
        elif action == 'open_left_door':
            allowed, reason = interlock.can_open_door()
            if allowed:
                side = interlock.get_allowed_door_side()
                if side != DoorSide.RIGHT:
                    manual.open_left_door()
                    recorder.record("操作", "开左门", pos, spd)
                else:
                    return jsonify({"status": "error", "message": "此处只能开右门"})
            else:
                return jsonify({"status": "error", "message": reason})
        elif action == 'open_right_door':
            allowed, reason = interlock.can_open_door()
            if allowed:
                side = interlock.get_allowed_door_side()
                if side != DoorSide.LEFT:
                    manual.open_right_door()
                    recorder.record("操作", "开右门", pos, spd)
                else:
                    return jsonify({"status": "error", "message": "此处只能开左门"})
            else:
                return jsonify({"status": "error", "message": reason})
        elif action == 'close_door':
            manual.close_door()
            recorder.record("操作", "关门", pos, spd)
        elif action == 'manual_mode':
            vehicle.running_mode = RunningMode.MANUAL
            recorder.record("操作", "切换为手动模式", pos, spd)
        elif action == 'auto_mode':
            vehicle.running_mode = RunningMode.AUTOMATIC
            next_station = engine.track.get_nearest_station_ahead(pos)
            if next_station:
                auto.set_target(next_station.position)
                recorder.record("操作", f"切换为自动驾驶，目标: {next_station.name}", pos, spd)
            else:
                return jsonify({"status": "error", "message": "无目标车站"})
        elif action == 'toggle_power':
            current = engine.power_supply.status
            if current == PowerStatus.NORMAL:
                engine.power_supply.set_status(PowerStatus.LOW_VOLTAGE)
            elif current == PowerStatus.LOW_VOLTAGE:
                engine.power_supply.set_status(PowerStatus.POWER_OFF)
            elif current == PowerStatus.POWER_OFF:
                engine.power_supply.set_status(PowerStatus.RECOVERING)
            else:
                engine.power_supply.set_status(PowerStatus.NORMAL)
        elif action == 'set_power':
            status_name = data.get('status', 'NORMAL')
            status_map = {
                'NORMAL': PowerStatus.NORMAL,
                'LOW_VOLTAGE': PowerStatus.LOW_VOLTAGE,
                'POWER_OFF': PowerStatus.POWER_OFF,
                'RECOVERING': PowerStatus.RECOVERING,
            }
            if status_name in status_map:
                engine.power_supply.set_status(status_map[status_name])
        else:
            return jsonify({"status": "error", "message": f"未知操作: {action}"})

    return jsonify({"status": "ok", "action": action})

@app.route('/api/start', methods=['POST'])
def api_start():
    """开始仿真"""
    engine.running = True
    engine.recorder.record("系统", "仿真开始")
    return jsonify({"status": "ok"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    """暂停仿真"""
    engine.running = False
    engine.recorder.record("系统", "仿真暂停")
    return jsonify({"status": "ok"})

@app.route('/api/track_geometry')
def api_track_geometry():
    """获取线路几何数据，用于 SVG 线路图渲染"""
    return jsonify(engine.get_track_geometry())

@app.route('/api/running')
def api_running():
    """查询仿真运行状态"""
    return jsonify({"running": engine.running})

# ---------------------------------------------------------------------------
# SocketIO — 实时推送
# ---------------------------------------------------------------------------

@socketio.on('connect')
def on_connect():
    """客户端连接时推送初始状态"""
    emit('state', engine.get_state())

@socketio.on('request_state')
def on_request_state():
    """客户端请求状态更新"""
    emit('state', engine.get_state())

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("  轨道交通模拟系统 — Web 版")
    print("  浏览器访问: http://localhost:5000")
    print("=" * 60)
    engine.running = True  # 默认自动运行
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
