"""
传感器数据模型 — Pydantic 请求/响应定义

参考: pc_receiver.py JSON 报文格式 v2.0
"""
from pydantic import BaseModel
from typing import Optional, List


class SensorData(BaseModel):
    """ESP32-P4 上报的传感器 JSON 数据"""
    type: str = "data"
    level: int = 0          # 0=正常, 1=预警, 2=严重, 3=紧急
    ts: int = 0             # FreeRTOS 毫秒时间戳
    dht11_t: Optional[float] = None
    dht11_h: Optional[float] = None
    ds18b20_t: Optional[float] = None
    mq135_v: Optional[float] = None
    light_v: Optional[float] = None
    mq135_do: Optional[int] = None    # 0=超阈值, 1=正常
    photo_do: Optional[int] = None    # 0=超阈值, 1=正常
    alert: int = 0          # 0=正常, 1=任一报警源触发
    err: int = 0            # 错误位掩码
    reason: str = ""


class SensorRecord(BaseModel):
    """数据库查询返回的传感器记录（含 id + 本地时间戳）"""
    id: int
    ts: str
    dht11_t: Optional[float] = None
    dht11_h: Optional[float] = None
    ds18b20_t: Optional[float] = None
    mq135_v: Optional[float] = None
    light_v: Optional[float] = None
    mq135_do: Optional[int] = None
    photo_do: Optional[int] = None
    level: int = 0
    alert: int = 0
    reason: str = ""
    err: int = 0


class StatusResponse(BaseModel):
    """/api/status 返回的当前状态"""
    level: int = 0
    dht11_t: Optional[float] = None
    dht11_h: Optional[float] = None
    ds18b20_t: Optional[float] = None
    mq135_v: Optional[float] = None
    light_v: Optional[float] = None
    mq135_do: Optional[int] = None
    photo_do: Optional[int] = None
    alert: int = 0
    reason: str = ""
    err: int = 0
    last_update: Optional[str] = None


class InventoryItem(BaseModel):
    """库存物料条目"""
    id: str
    name: str
    category: str = ""
    batch: str = ""
    spec: str = ""
    mfg_date: str = ""
    exp_date: str = ""
    checkin_time: str = ""
    checkin_temp: float = 0
    checkin_humi: float = 0
    status: str = "在库"


# ─── 出入库管理模型 ────────────────────────────────────────

class CheckinRequest(BaseModel):
    """入库请求 — 扫码结果 + 环境快照"""
    item_id: str                    # 物料编号 (二维码 JSON.id)
    name: str = ""                  # 品名
    category: str = ""              # 类别
    batch: str = ""                 # 批次号
    spec: str = ""                  # 规格
    mfg_date: str = ""              # 生产日期
    exp_date: str = ""              # 有效期
    env_temp: float = 0             # 入库时刻环境温度
    env_humi: float = 0             # 入库时刻环境湿度
    env_level: int = 0              # 入库时刻报警级别


class CheckoutRequest(BaseModel):
    """出库请求 — 仅需编号 + 环境"""
    item_id: str
    env_temp: float = 0
    env_humi: float = 0
    env_level: int = 0


class ScanResult(BaseModel):
    """扫码 API 返回值"""
    success: bool
    item: Optional[dict] = None     # 解码出的物料 JSON
    message: str = ""
    timestamp: str = ""


class WarehouseResponse(BaseModel):
    """出/入库操作响应"""
    ok: bool
    message: str = ""
    data: Optional[dict] = None     # 出库时返回完整物料信息


class CheckLogRecord(BaseModel):
    """出入库流水记录"""
    id: int
    item_id: str
    action: str                     # "入库" / "出库"
    timestamp: str
    env_temp: float = 0
    env_humi: float = 0
    env_level: int = 0


# ─── 报警事件模型 ──────────────────────────────────────────

class AlarmEvent(BaseModel):
    """单条报警事件记录"""
    id: int
    ts: str
    level: int                      # 1=预警, 2=严重, 3=紧急
    reason: str = ""
    dht11_t: Optional[float] = None
    dht11_h: Optional[float] = None
    ds18b20_t: Optional[float] = None
    mq135_v: Optional[float] = None
    mq135_do: Optional[int] = None
    light_v: Optional[float] = None
    photo_do: Optional[int] = None
    has_snapshot: bool = False      # 是否有现场照片
    acknowledged: int = 0           # 0=未确认, 1=已确认


class AlarmListResponse(BaseModel):
    """报警历史分页响应"""
    total: int
    page: int
    page_size: int
    items: list[AlarmEvent]


class AlarmDetailResponse(BaseModel):
    """报警详情（含快照 JPEG）"""
    event: AlarmEvent
    snapshot_b64: Optional[str] = None   # base64 编码的 JPEG


class AlarmSummary(BaseModel):
    """报警概要（用于仪表盘角标）"""
    total_count: int = 0
    unacknowledged: int = 0
    latest_level: int = 0
    latest_reason: str = ""


class StreamStatus(BaseModel):
    """摄像头流控状态"""
    enabled: bool
    online: bool
