"""
SmartMonitor Web 仪表盘 — FastAPI 入口

路由:
  POST  /api/sensors              ESP32-P4 上传传感器数据（经滤波）
  GET   /api/sensors/recent       最近 N 条记录
  GET   /api/inventory            库存清单
  GET   /api/status               当前最新状态
  POST  /api/warehouse/checkin    入库操作
  POST  /api/warehouse/checkout   出库操作
  GET   /api/warehouse/log        出入库流水
  GET   /api/camera/mjpeg         摄像头 MJPEG 实时流
  GET   /api/camera/snapshot      摄像头最新帧 JPEG
  POST  /api/camera/scan          摄像头帧二维码扫码
  WS    /ws                       WebSocket 实时推送
  GET   /                         仪表盘 HTML 页面
"""
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import os

from . import database
from . import filter as filt_module
from . import camera_server as cam_module
from .models import (
    SensorData, SensorRecord, StatusResponse, InventoryItem,
    CheckinRequest, CheckoutRequest, ScanResult,
    WarehouseResponse, CheckLogRecord,
)


# ==================== 全局实例 ====================
# 滤波器
sensor_filter = filt_module.SensorFilter()
print(f"[Filter] 滤波器已初始化 | enabled={sensor_filter.enabled}")

# 摄像头服务
cam = cam_module.get_camera_server()


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
    """启动时初始化数据库 + 启动摄像头 UDP 接收线程"""
    await database.init_db()
    print("[启动] 数据库初始化完成")

    # 启动摄像头服务
    cam.start()

    yield

    # 关闭摄像头
    cam_module.stop_camera()
    print("[关闭] 应用已停止")


# ==================== FastAPI 应用 ====================
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="SmartMonitor Dashboard", version="3.0", lifespan=lifespan)

# CORS — 开发阶段允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== HTTP API — 传感器 ====================

@app.post("/api/sensors", response_model=dict)
async def post_sensors(data: SensorData):
    """
    接收 ESP32-P4 传感器数据
    1. 滤波（范围截断 + EMA 平滑）
    2. 写入 SQLite
    3. 通过 WebSocket 广播给所有客户端
    """
    record = data.model_dump()

    # ── Step 1: 滤波 ──
    sensor_filter.apply(record)

    # ── Step 2: 写入数据库 ──
    row_id = await database.insert_sensor_data(record)

    # ── Step 3: 构造广播消息 ──
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
            "mq135_do": record.get("mq135_do"),
            "photo_do": record.get("photo_do"),
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
        mq135_do=row.get("mq135_do"),
        photo_do=row.get("photo_do"),
        alert=row.get("alert", 0),
        reason=row.get("reason", ""),
        err=row.get("err", 0),
        last_update=row.get("ts"),
    )


# ==================== HTTP API — 出入库管理 ====================

@app.post("/api/warehouse/checkin", response_model=WarehouseResponse)
async def warehouse_checkin(req: CheckinRequest):
    """入库操作"""
    item_dict = {
        "id": req.item_id,
        "name": req.name,
        "category": req.category,
        "batch": req.batch,
        "spec": req.spec,
        "mfg_date": req.mfg_date,
        "exp_date": req.exp_date,
    }
    env_dict = {
        "temp": req.env_temp,
        "humi": req.env_humi,
        "level": req.env_level,
    }

    ok = await database.do_checkin(item_dict, env_dict)
    if ok:
        return WarehouseResponse(ok=True, message=f"入库成功: {req.item_id}", data=item_dict)
    else:
        return WarehouseResponse(ok=False, message=f"重复入库: {req.item_id} 已在库中")


@app.post("/api/warehouse/checkout", response_model=WarehouseResponse)
async def warehouse_checkout(req: CheckoutRequest):
    """出库操作"""
    env_dict = {
        "temp": req.env_temp,
        "humi": req.env_humi,
        "level": req.env_level,
    }

    success, info = await database.do_checkout(req.item_id, env_dict)
    if success:
        return WarehouseResponse(ok=True, message=f"出库成功: {req.item_id}", data=info)
    else:
        return WarehouseResponse(ok=False, message=str(info))


@app.get("/api/warehouse/log", response_model=list[CheckLogRecord])
async def warehouse_log(limit: int = Query(default=20, ge=1, le=200)):
    """获取最近出入库流水记录"""
    logs = await database.get_check_log(limit)
    return logs


@app.get("/api/env-snapshot")
async def env_snapshot_api():
    """返回当前环境快照（供前端扫码后自动填充）"""
    env = await database.get_latest_env_snapshot()
    if env is None:
        return {"temp": None, "humi": None, "level": 0}
    return env


# ==================== HTTP API — 摄像头 ====================

@app.get("/api/camera/mjpeg")
async def camera_mjpeg():
    """MJPEG 实时视频流（浏览器 <img src="..."> 直接显示）"""
    return StreamingResponse(
        cam.mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=--frameboundary",
    )


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    """返回最新一帧 JPEG bytes 或占位图"""
    jpeg_data = cam.latest_jpeg or cam.placeholder_jpeg
    return Response(content=jpeg_data, media_type="image/jpeg")


@app.post("/api/camera/scan", response_model=ScanResult)
async def camera_scan():
    """
    对摄像头最新帧执行 pyzbar 二维码解码。
    返回解码出的物料 JSON（如果成功）。
    """
    if not cam.cv2_ok:
        return ScanResult(success=False, message="OpenCV 未安装，扫码不可用")

    frame = cam.latest_frame
    if frame is None:
        return ScanResult(success=False, message="无可用画面（摄像头可能未连接）")

    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        import cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        results = pyzbar_decode(gray)

        for r in results:
            data_str = r.data.decode("utf-8").strip()
            try:
                item = json.loads(data_str)
                if "id" in item:
                    import datetime as _dt
                    return ScanResult(
                        success=True,
                        item=item,
                        message=f"识别到: {item.get('id', '')}",
                        timestamp=_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
            except (ValueError, KeyError):
                continue

        return ScanResult(success=False, message="画面中未检测到有效二维码")
    except ImportError:
        return ScanResult(success=False, message="pyzbar 未安装")


@app.get("/api/camera/status")
async def camera_status():
    """返回摄像头连接状态信息"""
    return {
        "online": cam.online,
        "cv2_ok": cam.cv2_ok,
        "fps": round(cam.fps, 1),
        "total_frames": cam.total_frames,
        "running": cam.running,
    }


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 实时推送"""
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ==================== 静态页面 ====================

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    """返回仪表盘 HTML 页面"""
    dashboard_path = os.path.join(STATIC_DIR, "dashboard.html")
    if os.path.isfile(dashboard_path):
        return FileResponse(dashboard_path)
    return {"message": "SmartMonitor Dashboard API v3.0", "docs": "/docs"}
