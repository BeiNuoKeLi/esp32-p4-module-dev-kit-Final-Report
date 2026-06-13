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
  GET   /api/camera/stream        查询视频流开关状态
  POST  /api/camera/stream        切换视频流开关
  POST  /api/camera/scan          摄像头帧二维码扫码
  GET   /api/alarms               报警历史分页列表
  GET   /api/alarms/summary       报警概要统计
  GET   /api/alarms/{id}          报警详情（含快照）
  POST  /api/alarms/{id}/ack      确认报警
  WS    /ws                       WebSocket 实时推送
  GET   /                         仪表盘 HTML 页面
"""
import json
import asyncio
import time as _time
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
    AlarmEvent, AlarmListResponse, AlarmDetailResponse, AlarmSummary, StreamStatus,
    SimInjectRequest, SimStatus,
)


# ==================== 报警去重状态 ====================
_last_alarm_level: int = 0          # 上次触发的报警级别
_last_alarm_time: float = 0.0       # 上次触发的时间戳
DEDUP_WINDOW: float = 30.0          # 去重窗口（秒）


async def _maybe_record_alarm(record: dict):
    """
    检查去重规则，决定是否写入 alarm_events。
    规则:
      - level > 0 且 (级别变化 或 距上次同级别超过 30s) → 写入
      - level = 0 → 不写入
    """
    global _last_alarm_level, _last_alarm_time
    level = record.get("level", 0)
    now = _time.time()

    if level > 0 and (level != _last_alarm_level or now - _last_alarm_time > DEDUP_WINDOW):
        # 获取当前摄像头帧作为现场快照
        snapshot = cam.latest_jpeg or cam.placeholder_jpeg
        await database.insert_alarm_event(
            level=level,
            reason=record.get("reason", ""),
            dht11_t=record.get("dht11_t"),
            dht11_h=record.get("dht11_h"),
            ds18b20_t=record.get("ds18b20_t"),
            mq135_v=record.get("mq135_v"),
            mq135_do=record.get("mq135_do"),
            light_v=record.get("light_v"),
            photo_do=record.get("photo_do"),
            snapshot=snapshot,
        )
        _last_alarm_level = level
        _last_alarm_time = now
        print(f"[Alarm] 报警事件已记录 | level={level} | reason={record.get('reason', '')[:60]}")


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

# ★ ESP32-P4 设备 IP（仿真注入目标）
ESP32_IP = os.environ.get("ESP32_IP", "10.16.234.86")
ESP32_CMD_PORT = 8081
SIM_ACTIVE = False  # 轻量状态标志，仅用于前端显示，不再拦截数据


# ==================== 内置 UDP 监听器 (MCU → Docker 直通) ====================

class SensorUDPProtocol(asyncio.DatagramProtocol):
    """
    asyncio UDP 协议 — 监听 0.0.0.0:8080，直接接收 ESP32-P4 传感器 JSON。
    替代 udp_to_web.py 桥接脚本，消除外部依赖。
    """

    def datagram_received(self, data: bytes, addr: tuple):
        """收到 UDP 数据报 → 解析 JSON → 提交到传感器处理流水线"""
        try:
            raw = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            return
        if not raw:
            return

        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return

        # 仅处理 sensor data 类型（出入库仍通过 HTTP API 操作）
        msg_type = obj.get("type", "data")
        if msg_type == "data":
            # 提交到事件循环中处理（避免阻塞 UDP 收包）
            asyncio.get_event_loop().create_task(
                _process_udp_sensor(obj)
            )
        elif msg_type in ("checkin", "checkout"):
            # 出入库消息：转发到对应 HTTP API
            asyncio.get_event_loop().create_task(
                _process_udp_warehouse(obj, msg_type)
            )


async def _process_udp_sensor(obj: dict):
    """异步处理 UDP 收到的传感器数据 — 复用 POST /api/sensors 逻辑"""
    try:
        data = SensorData(**obj)
    except Exception:
        return  # 字段不合法，静默丢弃

    record = data.model_dump()

    # Step 1: 滤波
    sensor_filter.apply(record)

    # Step 2: 写入数据库
    row_id = await database.insert_sensor_data(record)

    # Step 2.5: 报警去重检测
    asyncio.create_task(_maybe_record_alarm(record))

    # Step 3: WebSocket 广播（始终广播 MCU 回传的真实/仿真数据）
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
    asyncio.create_task(manager.broadcast(broadcast_msg))


async def _process_udp_warehouse(obj: dict, msg_type: str):
    """异步处理 UDP 收到的出入库消息 → 转发到对应数据库操作"""
    try:
        if msg_type == "checkin":
            item = {
                "id": obj.get("item_id", ""),
                "name": obj.get("name", ""),
                "category": obj.get("category", ""),
                "batch": obj.get("batch", ""),
                "spec": obj.get("spec", ""),
                "mfg_date": obj.get("mfg_date", ""),
                "exp_date": obj.get("exp_date", ""),
            }
            env = {
                "temp": obj.get("env_temp", 0),
                "humi": obj.get("env_humi", 0),
                "level": obj.get("env_level", 0),
            }
            await database.do_checkin(item, env)
        elif msg_type == "checkout":
            env = {
                "temp": obj.get("env_temp", 0),
                "humi": obj.get("env_humi", 0),
                "level": obj.get("env_level", 0),
            }
            await database.do_checkout(obj.get("item_id", ""), env)
    except Exception:
        pass  # 静默丢弃格式不正确的出入库消息


# ==================== 应用生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库 + 启动摄像头 UDP + 传感器 UDP 监听"""
    await database.init_db()
    print("[启动] 数据库初始化完成")

    # 启动摄像头服务
    cam.start()

    # ★ 启动内置 UDP 传感器监听器（替代 udp_to_web.py 桥接）
    loop = asyncio.get_event_loop()
    _udp_transport = None
    try:
        _udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: SensorUDPProtocol(),
            local_addr=("0.0.0.0", 8080),
        )
        print("[UDP] 传感器监听已启动 → 0.0.0.0:8080（容器内置，无需外部桥接）")
    except OSError as e:
        print(f"[UDP] 警告: 无法绑定 0.0.0.0:8080 — {e}")
        print("  传感器数据将只能通过 HTTP POST /api/sensors 接收")

    yield

    # 关闭摄像头
    cam_module.stop_camera()
    # 关闭 UDP 监听
    if _udp_transport is not None:
        try:
            _udp_transport.close()
        except Exception:
            pass
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

    # ── Step 2.5: 报警去重检测 ──
    asyncio.create_task(_maybe_record_alarm(record))

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


@app.get("/api/inventory/stats")
async def inventory_stats():
    """获取库存分类统计（氮肥/钾肥/复合肥）"""
    stats = await database.get_inventory_stats()
    # 构建总计
    total_all = sum(s["total"] for s in stats)
    in_stock_all = sum(s["in_stock"] for s in stats)
    return {
        "categories": stats,
        "total": {"total": total_all, "in_stock": in_stock_all},
    }


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """获取当前最新传感器状态（始终从 DB 读 MCU 回传的真实/仿真数据）"""
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


@app.delete("/api/warehouse/log")
async def warehouse_log_clear():
    """清空全部出入库流水记录"""
    deleted = await database.clear_check_log()
    return {"ok": True, "deleted": deleted, "message": f"已清空 {deleted} 条流水记录"}


@app.delete("/api/alarms")
async def alarm_clear():
    """清空全部报警事件记录"""
    deleted = await database.clear_alarm_events()
    return {"ok": True, "deleted": deleted, "message": f"已清空 {deleted} 条报警记录"}


@app.delete("/api/inventory/{item_id}")
async def inventory_delete(item_id: str):
    """删除库存中指定物料（同时删关联流水）"""
    ok = await database.delete_inventory_item(item_id)
    if ok:
        return {"ok": True, "message": f"已删除: {item_id}"}
    return {"ok": False, "message": f"物料 {item_id} 不存在"}


@app.post("/api/inventory/add")
async def inventory_add(item: InventoryItem):
    """手动新增库存物料"""
    item_dict = item.model_dump()
    ok, msg = await database.add_inventory_item(item_dict)
    return {"ok": ok, "message": msg}


@app.get("/api/env-snapshot")
async def env_snapshot_api():
    """返回当前环境快照（供前端扫码后自动填充）"""
    env = await database.get_latest_env_snapshot()
    if env is None:
        return {"temp": None, "humi": None, "level": 0}
    return env


# ==================== HTTP API — 仿真注入 (转发到 MCU) ====================

@app.get("/api/sim/status", response_model=SimStatus)
async def sim_status():
    """查询仿真注入当前状态"""
    return SimStatus(
        active=SIM_ACTIVE,
        esp_ip=ESP32_IP,
        message="仿真模式: ESP32 将使用注入值替代真实传感器" if SIM_ACTIVE else "真实传感器模式",
    )


@app.post("/api/sim/inject")
async def sim_inject(data: SimInjectRequest):
    """
    注入仿真传感器数据 → 转发到 ESP32-P4 UDP 8081。
    MCU 收到后替代真实传感器读数，通过 UDP 8080 回传处理后数据。
    Web 端通过正常的 UDP 8080 → DB → WebSocket 流程获取更新。
    """
    # 构建 MCU 期望的 JSON（与 smart_monitor_sim_gui.py _apply_sim 一致）
    cmd = {
        "dht11_t": data.dht11_t,
        "dht11_h": data.dht11_h,
        "ds18b20_t": round(data.ds18b20_t, 2),
        "mq135_v": round(data.mq135_v, 2),
        "mq135_do": data.mq135_do,
        "photo_raw": data.photo_raw,
        "photo_do": data.photo_do,
    }
    msg = json.dumps(cmd)

    # 通过 UDP 发送到 ESP32 命令端口 8081
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(msg.encode("utf-8"), (ESP32_IP, ESP32_CMD_PORT))
        print(f"[Sim] → 发送仿真命令到 {ESP32_IP}:{ESP32_CMD_PORT} | {msg}")

        # 等待 MCU 确认
        try:
            ack_data, _ = sock.recvfrom(512)
            ack_text = ack_data.decode("utf-8").strip()
            print(f"[Sim] ← MCU 确认: {ack_text}")
        except socket.timeout:
            print("[Sim] ⚠️ 未收到 MCU 确认 (超时)")

        sock.close()
    except OSError as e:
        print(f"[Sim] ❌ UDP 发送失败: {e}")
        return {"ok": False, "message": f"无法连接到 ESP32 ({ESP32_IP}:{ESP32_CMD_PORT}): {e}"}

    global SIM_ACTIVE
    SIM_ACTIVE = True
    return {"ok": True, "message": f"仿真命令已发送到 ESP32 [{ESP32_IP}], MCU 将回传处理后的数据"}


@app.post("/api/sim/reset")
async def sim_reset():
    """
    发送 reset 命令到 ESP32-P4 UDP 8081。
    MCU 收到后关闭仿真模式，恢复真实传感器读数。
    """
    msg = '{"cmd":"reset"}'

    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(msg.encode("utf-8"), (ESP32_IP, ESP32_CMD_PORT))
        print(f"[Sim] → 发送 reset 到 {ESP32_IP}:{ESP32_CMD_PORT}")

        try:
            ack_data, _ = sock.recvfrom(512)
            ack_text = ack_data.decode("utf-8").strip()
            print(f"[Sim] ← MCU 确认: {ack_text}")
        except socket.timeout:
            print("[Sim] ⚠️ 未收到 MCU reset 确认 (超时)")

        sock.close()
    except OSError as e:
        print(f"[Sim] ❌ Reset UDP 发送失败: {e}")
        return {"ok": False, "message": f"无法连接到 ESP32: {e}"}

    global SIM_ACTIVE
    SIM_ACTIVE = False
    return {"ok": True, "message": "已发送 reset 到 ESP32, MCU 恢复真实传感器模式"}


# ==================== HTTP API — 摄像头 ====================

@app.get("/api/camera/mjpeg")
async def camera_mjpeg():
    """MJPEG 实时视频流（浏览器 <img src="..."> 直接显示）"""
    return StreamingResponse(
        cam.mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=--frameboundary",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",       # 禁用 nginx 缓冲
        },
    )


@app.get("/api/camera/snapshot")
async def camera_snapshot():
    """返回最新一帧 JPEG bytes 或占位图"""
    jpeg_data = cam.latest_jpeg or cam.placeholder_jpeg
    return Response(
        content=jpeg_data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
        # ★ CLAHE 局部直方图均衡 → 增强二维码边缘对比度，抵消 JPEG 压缩模糊
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
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
        "mode": cam_module.CAMERA_MODE,
        "stream_url": cam_module.ESP32_CAM_STREAM_URL if cam_module.CAMERA_MODE == "http" else None,
        "capture_url": cam_module.ESP32_CAM_URL if cam_module.CAMERA_MODE == "http" else None,
        "online": cam.online,
        "cv2_ok": cam.cv2_ok,
        "fps": round(cam.fps, 1),
        "total_frames": cam.total_frames,
        "running": cam.running,
        "stream_enabled": cam.stream_enabled,
        "chunks_received": cam.chunk_count,
        "frames_timeout": cam.timeout_count,
    }


@app.get("/api/camera/stream", response_model=StreamStatus)
async def camera_stream_get():
    """查询视频流开关状态"""
    return StreamStatus(enabled=cam.stream_enabled, online=cam.online)


@app.post("/api/camera/stream", response_model=StreamStatus)
async def camera_stream_toggle():
    """切换视频流开关（同时暂停/恢复 UDP 接收以节省流量）"""
    cam.stream_enabled = not cam.stream_enabled
    if cam.stream_enabled:
        cam.resume()
    else:
        cam.pause()
    print(f"[Camera] 视频流已{'开启' if cam.stream_enabled else '关闭'}")
    return StreamStatus(enabled=cam.stream_enabled, online=cam.online)


# ==================== HTTP API — 报警历史 ====================

@app.get("/api/alarms/summary", response_model=AlarmSummary)
async def alarm_summary():
    """获取报警概要统计"""
    s = await database.get_alarm_summary()
    return AlarmSummary(**s)


@app.get("/api/alarms", response_model=AlarmListResponse)
async def alarm_list(page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=20, ge=1, le=100),
                     level: int = Query(default=0, ge=0, le=3)):
    """分页查询报警历史，可选按 level 筛选（0=全部）"""
    filter_lv = level if level > 0 else None
    items_raw, total = await database.get_alarm_events(page, page_size, filter_lv)
    return AlarmListResponse(
        total=total, page=page, page_size=page_size,
        items=[AlarmEvent(**it) for it in items_raw]
    )


@app.get("/api/alarms/{alarm_id}", response_model=AlarmDetailResponse)
async def alarm_detail(alarm_id: int):
    """获取单条报警详情（含 base64 编码的现场快照）"""
    detail = await database.get_alarm_event_detail(alarm_id)
    if detail is None:
        return AlarmDetailResponse(event=None, snapshot_b64=None)
    # 分离快照和事件字段
    snapshot_b64 = detail.pop("snapshot_b64", None)
    has = detail.pop("has_snapshot", False)
    event = AlarmEvent(**detail)
    return AlarmDetailResponse(event=event, snapshot_b64=snapshot_b64)


@app.post("/api/alarms/{alarm_id}/ack")
async def alarm_ack(alarm_id: int):
    """确认一条报警"""
    ok = await database.acknowledge_alarm(alarm_id)
    return {"ok": ok, "id": alarm_id}


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
