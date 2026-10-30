"""FastAPI application: REST + WebSocket front door for the sim engine,
and static hosting of the sand-table dashboard.

Bind host/port are controlled by env vars so the .bat launcher can set them:
  ANANKOS_HOST (default 0.0.0.0 so Tailscale/LAN can reach it)
  ANANKOS_PORT (default 47811, an uncommon high port)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import SimEngine
from .modbus import ModbusServer

WEB = Path(__file__).parent.parent / "web"

app = FastAPI(title="Anankos Sim Lab", version="0.2.0")
engine = SimEngine()

# Tick rate: how often (seconds) one simulated minute passes while running.
TICK_SECONDS = float(os.environ.get("ANANKOS_TICK_SECONDS", "1.0"))
# Simulated OT protocol surface (Modbus/TCP) for external tooling.
MODBUS_HOST = os.environ.get("ANANKOS_MODBUS_HOST", "0.0.0.0")
MODBUS_PORT = int(os.environ.get("ANANKOS_MODBUS_PORT", "15020"))
MODBUS_ENABLED = os.environ.get("ANANKOS_MODBUS", "1") != "0"


class Hub:
    """Tracks connected dashboards and broadcasts state."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, payload):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


hub = Hub()


modbus_server = ModbusServer(engine, MODBUS_HOST, MODBUS_PORT,
                             on_change=lambda: hub.broadcast(engine.snapshot()))


@app.on_event("startup")
async def _start_loop():
    asyncio.create_task(_sim_loop())
    if MODBUS_ENABLED:
        try:
            await modbus_server.start()
            print(f"[modbus] simulated Modbus/TCP endpoint on {MODBUS_HOST}:{MODBUS_PORT}")
        except Exception as e:  # noqa: BLE001
            print(f"[modbus] disabled - could not bind {MODBUS_PORT}: {e}")


async def _sim_loop():
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if engine.running:
            engine.step()
            await hub.broadcast(engine.snapshot())


# --- REST API ---------------------------------------------------------------
@app.get("/api/state")
def get_state():
    return engine.snapshot()


@app.get("/api/techniques")
def get_techniques():
    return {"techniques": list(engine.techniques.values())}


@app.post("/api/sim/{action}")
async def sim_control(action: str):
    if action == "start":
        engine.running = True
        engine._log("operator", "SIM", "Simulation started.", "info")
    elif action == "pause":
        engine.running = False
        engine._log("operator", "SIM", "Simulation paused.", "info")
    elif action == "reset":
        engine.reset()
    else:
        return {"ok": False, "error": "unknown action"}
    await hub.broadcast(engine.snapshot())
    return {"ok": True, "running": engine.running}


@app.post("/api/execute")
async def execute(payload: dict):
    res = engine.execute(
        payload.get("technique_id"),
        payload.get("device_id"),
        payload.get("params") or {},
    )
    await hub.broadcast(engine.snapshot())
    return res


@app.post("/api/restore/{device_id}")
async def restore(device_id: str):
    res = engine.restore_device(device_id)
    await hub.broadcast(engine.snapshot())
    return res


@app.post("/api/manual")
async def manual(payload: dict):
    res = engine.manual_set(
        payload.get("device_id"),
        payload.get("param"),
        payload.get("value"),
    )
    await hub.broadcast(engine.snapshot())
    return res


# --- assessor authoring (mirror ground-truth findings) ----------------------
@app.post("/api/finding")
async def finding(payload: dict):
    res = engine.set_finding(
        payload.get("facility_id"), payload.get("key"), payload.get("value"))
    await hub.broadcast(engine.snapshot())
    return res


@app.post("/api/device_flag")
async def device_flag(payload: dict):
    res = engine.set_device_flag(
        payload.get("device_id"), payload.get("flag"), payload.get("value"))
    await hub.broadcast(engine.snapshot())
    return res


@app.get("/api/protocol")
def protocol_info():
    return {
        "enabled": MODBUS_ENABLED,
        "protocol": "Modbus/TCP",
        "host": MODBUS_HOST,
        "port": MODBUS_PORT,
        "note": "Unauthenticated OT protocol surface for external tooling. Simulation only.",
        "map": modbus_server.regs.describe(),
    }


@app.get("/api/scenario")
def scenario_export():
    return engine.export_scenario()


@app.post("/api/scenario")
async def scenario_import(payload: dict):
    res = engine.import_scenario(payload)
    await hub.broadcast(engine.snapshot())
    return res


# --- WebSocket --------------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.join(ws)
    try:
        await ws.send_json(engine.snapshot())
        while True:
            await ws.receive_text()  # keepalive; client isn't required to send
    except WebSocketDisconnect:
        hub.leave(ws)
    except Exception:
        hub.leave(ws)


# --- Static dashboard -------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
