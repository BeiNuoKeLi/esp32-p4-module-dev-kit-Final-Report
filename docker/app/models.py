"""
传感器数据模型 — Pydantic 请求/响应定义

参考: pc_receiver.py JSON 报文格式 v2.0
"""
from pydantic import BaseModel
from typing import Optional


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
