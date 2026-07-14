"""Web ATS 的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .simulation import SimulationRuntime


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web-ats"
runtime = SimulationRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="轨道交通仿真 Web ATS API", lifespan=lifespan)


@app.middleware("http")
async def disable_frontend_cache(request, call_next):
    """开发态前端始终读取最新页面，避免合并后仍执行旧脚本。"""
    result = await call_next(request)
    if (request.url.path == "/" or request.url.path.endswith(".html")
            or request.url.path.startswith("/scripts/")
            or request.url.path.startswith("/styles/")):
        result.headers["Cache-Control"] = "no-store, max-age=0"
    return result


class AddTrainRequest(BaseModel):
    train_id: str = Field(min_length=1, max_length=20)
    station_id: int
    direction: int = 1
    plan_id: str = "mainline_loop"


class PlanRequest(BaseModel):
    plan_id: str = "mainline_loop"


class PowerRequest(BaseModel):
    status: str


class DoorRequest(BaseModel):
    side: str  # "left", "right", "close"


class ModeRequest(BaseModel):
    mode: str  # "manual", "auto"


class ControlLevelRequest(BaseModel):
    level: str  # "FULL_TRACTION", "MEDIUM_TRACTION", "LOW_TRACTION", "COAST", "SERVICE_BRAKE", "FULL_BRAKE", "EMERGENCY_BRAKE"


class LoadLevelRequest(BaseModel):
    level: str  # "AW0", "AW1", "AW2", "AW3"


class DwellTimeRequest(BaseModel):
    seconds: float


class WeatherRequest(BaseModel):
    weather: str  # "DRY", "RAIN", "SNOW"


class ConsistPresetRequest(BaseModel):
    preset: str  # "4M2T", "6M0T", "1M4T"


class RouteRequest(BaseModel):
    route_id: str


class SwitchRouteRequest(BaseModel):
    component_ids: list[int] = Field(min_length=1)
    label: str = Field(default="", max_length=40)


class StationJumpRequest(BaseModel):
    station_id: str


class NetworkToggleRequest(BaseModel):
    enabled: bool


class TrackSourceRequest(BaseModel):
    source: str  # "database" | "demo"


class PlcOutputRequest(BaseModel):
    atp_safe_out: int = Field(default=0, ge=0, le=65535)


class TrainControlRequest(BaseModel):
    action: str
    value: str | None = None


def response(result: dict):
    return JSONResponse(result, status_code=200 if result["ok"] else 409)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "web-ats"}


@app.get("/api/snapshot")
async def snapshot():
    return runtime.snapshot()


@app.post("/api/trains")
async def add_train(body: AddTrainRequest):
    result = runtime.command(lambda: runtime.dispatch.add_train(
        body.train_id, body.station_id, body.direction))
    if result["ok"] and body.plan_id:
        result = runtime.command(lambda: runtime.dispatch.assign_plan(
            body.train_id, body.plan_id))
    return response(result)


@app.delete("/api/trains/{train_id}")
async def remove_train(train_id: str):
    return response(runtime.command(
        lambda: runtime.dispatch.remove_train(train_id)))


@app.post("/api/trains/{train_id}/plan")
async def assign_plan(train_id: str, body: PlanRequest):
    return response(runtime.command(
        lambda: runtime.dispatch.assign_plan(train_id, body.plan_id)))


@app.post("/api/power")
async def set_power(body: PowerRequest):
    return response(runtime.set_power(body.status))


@app.post("/api/trains/{train_id}/doors")
async def door_control(train_id: str, body: DoorRequest):
    result = runtime.door_command(train_id, body.side)
    runtime.record_web_command(train_id, f"door-{body.side}", result)
    return response(result)


@app.post("/api/trains/{train_id}/mode")
async def set_mode(train_id: str, body: ModeRequest):
    result = runtime.set_running_mode(train_id, body.mode)
    runtime.record_web_command(train_id, f"mode-{body.mode}", result)
    return response(result)


@app.post("/api/trains/{train_id}/control-level")
async def set_control_level(train_id: str, body: ControlLevelRequest):
    result = runtime.apply_control_level(train_id, body.level)
    runtime.record_web_command(train_id, f"handle-{body.level}", result)
    return response(result)


@app.post("/api/trains/{train_id}/load-level")
async def set_load_level(train_id: str, body: LoadLevelRequest):
    result = runtime.set_load_level(train_id, body.level)
    runtime.record_web_command(train_id, f"load-{body.level}", result)
    return response(result)


@app.post("/api/trains/{train_id}/dwell-time")
async def set_dwell_time(train_id: str, body: DwellTimeRequest):
    result = runtime.set_dwell_time(train_id, body.seconds)
    runtime.record_web_command(train_id, f"dwell-{body.seconds}", result)
    return response(result)


@app.post("/api/environment")
async def set_weather(body: WeatherRequest):
    return response(runtime.set_weather(body.weather))


@app.post("/api/simulation/fast-forward/{train_id}")
async def fast_forward(train_id: str):
    result = runtime.fast_forward_to_next_station(train_id)
    runtime.record_web_command(train_id, "fast-forward", result)
    return response(result)


@app.get("/api/scenes")
async def list_scenes():
    scenes = [
        {"id": scene_id, **scene}
        for scene_id, scene in runtime.scenes.items()
    ]
    return {
        "ok": True,
        "scenes": scenes,
        "current": runtime.current_scene,
    }


@app.post("/api/scenes/{scene_id}/apply")
async def apply_scene(scene_id: str):
    return response(runtime.apply_scene(scene_id))


@app.get("/api/evaluation")
async def evaluation():
    """返回远程 main 新增的完整事件与评价数据，避免覆盖实时回放接口。"""
    return runtime.replay_data()


@app.post("/api/network/start")
async def network_start():
    return response(runtime.network_connect())


@app.post("/api/network/stop")
async def network_stop():
    return response(runtime.network_disconnect())


@app.post("/api/cab_display/start")
async def cab_display_start():
    return response(runtime.cab_connect())


@app.post("/api/cab_display/stop")
async def cab_display_stop():
    return response(runtime.cab_disconnect())


@app.get("/api/cab_display")
async def cab_display_get():
    return JSONResponse(runtime.cab_display_state)


@app.post("/api/cab_display")
async def cab_display_post(body: Request):
    updates = await body.json()
    return response(runtime.set_cab_display(updates))


@app.post("/api/scenarios/{scenario_id}")
async def apply_scenario(scenario_id: str):
    return response(runtime.apply_scenario(scenario_id))


@app.get("/api/replay")
async def replay():
    return runtime.replay_snapshot()


@app.post("/api/simulation/{command}")
async def simulation_command(command: str):
    if command == "pause":
        runtime.paused = True
    elif command == "resume":
        runtime.paused = False
    elif command in {"1", "2", "5", "10"}:
        runtime.speed_multiplier = int(command)
    else:
        return JSONResponse(
            {"ok": False, "message": f"未知仿真命令: {command}"},
            status_code=404,
        )
    return {"ok": True, "message": "仿真状态已更新"}


# ── Phase 4: 图表与力表格 ──────────────────────────────

@app.get("/api/trains/{train_id}/chart/speed-force")
async def speed_force_chart(train_id: str):
    return runtime.get_chart_speed_force(train_id)


@app.get("/api/trains/{train_id}/chart/energy")
async def energy_chart(train_id: str):
    return runtime.get_chart_energy(train_id)


@app.get("/api/trains/{train_id}/force-table")
async def force_table(train_id: str):
    return runtime.get_force_table(train_id)


# ── Phase 5: 编组配置 ──────────────────────────────────

@app.post("/api/trains/{train_id}/consist/preset")
async def consist_preset(train_id: str, body: ConsistPresetRequest):
    result = runtime.apply_consist_preset(train_id, body.preset)
    runtime.record_web_command(train_id, f"consist-{body.preset}", result)
    return response(result)


@app.get("/api/trains/{train_id}/consist")
async def get_consist(train_id: str):
    return runtime.get_consist_config(train_id)


# ── Phase 6: 交路选择与车站跳转 ─────────────────────────

@app.get("/api/trains/{train_id}/routes")
async def get_routes(train_id: str):
    return runtime.get_available_routes(train_id)


@app.post("/api/trains/{train_id}/route")
async def set_route(train_id: str, body: RouteRequest):
    result = runtime.set_route(train_id, body.route_id)
    runtime.record_web_command(train_id, f"route-{body.route_id}", result)
    return response(result)


@app.post("/api/trains/{train_id}/switch-route")
async def request_switch_route(train_id: str, body: SwitchRouteRequest):
    """办理并锁闭一条经过指定真实道岔组件的进路。"""
    return response(runtime.request_switch_route(
        train_id, body.component_ids, body.label))


@app.delete("/api/trains/{train_id}/switch-route")
async def cancel_switch_route(train_id: str):
    """取消列车尚未进入的人工岔道进路。"""
    return response(runtime.cancel_switch_route(train_id))


@app.post("/api/trains/{train_id}/jump-station")
async def jump_station(train_id: str, body: StationJumpRequest):
    result = runtime.jump_to_station(train_id, body.station_id)
    runtime.record_web_command(train_id, f"jump-{body.station_id}", result)
    return response(result)


@app.post("/api/trains/{train_id}/{command}")
async def train_command(train_id: str, command: str):
    commands = {
        "depart": runtime.dispatch.depart,
        "hold": runtime.dispatch.hold,
        "release": runtime.dispatch.release,
        "emergency-stop": runtime.dispatch.emergency_stop,
        "restore": runtime.dispatch.restore,
    }
    action = commands.get(command)
    if action is None:
        return JSONResponse(
            {"ok": False, "message": f"未知列车命令: {command}"},
            status_code=404,
        )
    result = runtime.command(lambda: action(train_id))
    runtime.record_web_command(train_id, command, result)
    return response(result)


# ── Phase 7: 网络开关 ──────────────────────────────────

@app.post("/api/network/toggle")
async def network_toggle(body: NetworkToggleRequest):
    return response(runtime.toggle_network(body.enabled))


@app.post("/api/trains/{train_id}/control/action")
async def train_control(train_id: str, body: TrainControlRequest):
    return response(runtime.control_train(train_id, body.action, body.value))


@app.post("/api/power")
async def set_power(body: PowerRequest):
    return response(runtime.set_power(body.status))


# ── 数据源切换 ──────────────────────────────────────

@app.get("/api/track/source")
async def get_track_source():
    return {"ok": True, "source": runtime.data_source}


@app.post("/api/track/source")
async def switch_track_source(body: TrackSourceRequest):
    return response(runtime.switch_track_source(body.source))


@app.get("/api/plc/output")
async def plc_output_get():
    return {
        "ok": True,
        "state": dict(runtime.plc_output_state),
        "manual_override": runtime.plc_output_manual_override,
        "vehicle_speed": runtime.plc_output_vehicle_speed,
    }


@app.post("/api/plc/output")
async def plc_output_post(body: Request):
    """兼容指示灯状态更新和 ATP 数值输出两种司机台报文。"""
    updates = await body.json()
    if "atp_safe_out" in updates:
        value = PlcOutputRequest(**updates).atp_safe_out
        try:
            result = runtime.set_plc_raw_output(value)
            if not result["ok"]:
                raise RuntimeError(result["message"])
            runtime.recorder.record(
                "PLC输出",
                f"ATP安全输出: 0x{value:04X}",
                source="web-ats",
                severity="INFO",
            )
            return result
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "message": f"PLC输出发送失败: {exc}"},
                status_code=500,
            )
    return response(runtime.set_plc_output(updates))


@app.post("/api/scenarios/{scenario_id}")
async def apply_scenario(scenario_id: str):
    return response(runtime.apply_scenario(scenario_id))


@app.get("/api/replay")
async def replay():
    return runtime.replay_snapshot()


@app.post("/api/simulation/{command}")
async def simulation_command(command: str):
    if command == "pause":
        runtime.paused = True
    elif command == "resume":
        runtime.paused = False
    elif command in {"1", "2", "5", "10"}:
        runtime.speed_multiplier = int(command)
    else:
        return JSONResponse(
            {"ok": False, "message": f"未知仿真命令: {command}"},
            status_code=404,
        )
    return {"ok": True, "message": "仿真状态已更新"}


@app.websocket("/ws/realtime")
async def realtime(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(runtime.snapshot())
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, RuntimeError):
        return


# API 与 WebSocket 路由必须先注册，静态站点最后挂载。
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
