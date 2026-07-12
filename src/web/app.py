"""Web ATS 的 FastAPI 入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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


@app.post("/api/power")
async def set_power(body: PowerRequest):
    return response(runtime.set_power(body.status))


@app.post("/api/network/start")
async def network_start():
    return response(runtime.network_connect())


@app.post("/api/network/stop")
async def network_stop():
    return response(runtime.network_disconnect())


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
    elif command in {"1", "2", "5"}:
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
