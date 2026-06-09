"""
SmartMonitor Web 仪表盘 — FastAPI 入口

路由:
  POST  /api/sensors          ESP32-P4 上传传感器数据
  GET   /api/sensors/recent   最近 N 条记录
  GET   /api/inventory        库存清单
  GET   /api/status           当前最新状态
  WS    /ws                   WebSocket 实时推送
  GET   /                     仪表盘 HTML 页面
"""
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from . import database
from .models import SensorData, SensorRecord, StatusResponse, InventoryItem


# ==================== WebSocket 连接管理器 ====================
class ConnectionManager:
    """管理所有 WebSocket 连接，支持广播"""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        """向所有已连接客户端广播消息"""
        payload = json.dumps(message, ensure_ascii=False)
        stale = []
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


# ==================== 应用生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库"""
    await database.init_db()
    print("[启动] 数据库初始化完成")
    yield
    print("[关闭] 应用已停止")


# ==================== FastAPI 应用 ====================
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="SmartMonitor Dashboard", version="2.0", lifespan=lifespan)

# CORS — 开发阶段允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== HTTP API ====================

@app.post("/api/sensors", response_model=dict)
async def post_sensors(data: SensorData):
    """
    接收 ESP32-P4 传感器数据
    1. 写入 SQLite
    2. 通过 WebSocket 广播给所有客户端
    """
    record = data.model_dump()

    # 写入数据库
    row_id = await database.insert_sensor_data(record)

    # 构造广播消息
    import datetime
    broadcast_msg = {
        "type": "sensor_update",
        "id": row_id,
        "data": {
            "dht11_t": record.get("dht11_t"),
            "dht11_h": record.get("dht11_h"),
            "ds18b20_t": record.get("ds18b20_t"),
            "mq135_v": record.get("mq135_v"),
            "light_v": record.get("light_v"),
            "level": record.get("level", 0),
            "alert": record.get("alert", 0),
            "reason": record.get("reason", ""),
            "err": record.get("err", 0),
        },
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 广播（不阻塞请求响应）
    asyncio.create_task(manager.broadcast(broadcast_msg))

    return {"ok": True, "id": row_id}


@app.get("/api/sensors/recent", response_model=list[SensorRecord])
async def get_recent(limit: int = Query(default=60, ge=1, le=500)):
    """获取最近 N 条传感器记录（默认 60 条）"""
    rows = await database.get_recent_sensor_data(limit)
    return rows


@app.get("/api/inventory", response_model=list[InventoryItem])
async def get_inventory_api(status: str | None = Query(default=None)):
    """获取库存清单（可选 ?status=在库 过滤）"""
    rows = await database.get_inventory(status)
    return rows


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """获取当前最新传感器状态"""
    row = await database.get_latest_status()
    if row is None:
        return StatusResponse()
    return StatusResponse(
        level=row.get("level", 0),
        dht11_t=row.get("dht11_t"),
        dht11_h=row.get("dht11_h"),
        ds18b20_t=row.get("ds18b20_t"),
        mq135_v=row.get("mq135_v"),
        light_v=row.get("light_v"),
        alert=row.get("alert", 0),
        reason=row.get("reason", ""),
        err=row.get("err", 0),
        last_update=row.get("ts"),
    )


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 实时推送"""
    await manager.connect(ws)
    try:
        while True:
            # 保持连接，接收客户端消息（心跳等）
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ==================== 静态页面 ====================

# 挂载 static 目录（CSS / JS 等资源）
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    """返回仪表盘 HTML 页面"""
    dashboard_path = os.path.join(STATIC_DIR, "dashboard.html")
    if os.path.isfile(dashboard_path):
        return FileResponse(dashboard_path)
    return {"message": "SmartMonitor Dashboard API", "docs": "/docs"}
