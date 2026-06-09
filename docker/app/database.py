"""
SQLite 数据库操作层 — 异步（aiosqlite）

表:
  - sensor_data:  传感器时间序列
  - inventory:    物料库存（复用 warehouse_db.py 结构）
  - check_log:    出入库流水（复用 warehouse_db.py 结构）
"""
import aiosqlite
import os
import datetime
from typing import Optional

# 数据库文件路径（容器内 /app/data/warehouse.db，宿主机 docker/data/warehouse.db）
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "warehouse.db")


def _ensure_dir():
    """确保数据目录存在"""
    os.makedirs(DB_DIR, exist_ok=True)


async def init_db():
    """初始化数据库表（幂等）"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 传感器数据表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sensor_data (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                dht11_t   REAL,
                dht11_h   REAL,
                ds18b20_t REAL,
                mq135_v   REAL,
                light_v   REAL,
                level     INTEGER DEFAULT 0,
                alert     INTEGER DEFAULT 0,
                reason    TEXT DEFAULT '',
                err       INTEGER DEFAULT 0
            )
        """)
        # 库存表（复用 warehouse_db.py 结构）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                category      TEXT DEFAULT '',
                batch         TEXT DEFAULT '',
                spec          TEXT DEFAULT '',
                mfg_date      TEXT DEFAULT '',
                exp_date      TEXT DEFAULT '',
                checkin_time  TEXT DEFAULT '',
                checkin_temp  REAL DEFAULT 0,
                checkin_humi  REAL DEFAULT 0,
                status        TEXT DEFAULT '在库'
            )
        """)
        # 出入库流水表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS check_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id   TEXT NOT NULL,
                action    TEXT NOT NULL,
                timestamp TEXT DEFAULT '',
                env_temp  REAL DEFAULT 0,
                env_humi  REAL DEFAULT 0,
                env_level INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def insert_sensor_data(data: dict) -> int:
    """插入一条传感器记录，返回 id"""
    _ensure_dir()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            INSERT INTO sensor_data (ts, dht11_t, dht11_h, ds18b20_t, mq135_v, light_v, level, alert, reason, err)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            data.get("dht11_t"), data.get("dht11_h"),
            data.get("ds18b20_t"), data.get("mq135_v"),
            data.get("light_v"), data.get("level", 0),
            data.get("alert", 0), data.get("reason", ""),
            data.get("err", 0)
        ))
        await db.commit()
        return cursor.lastrowid


async def get_recent_sensor_data(limit: int = 60) -> list[dict]:
    """获取最近 N 条传感器记录"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sensor_data ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]


async def get_latest_status() -> Optional[dict]:
    """获取最新一条传感器状态"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_inventory(status_filter: Optional[str] = None) -> list[dict]:
    """获取库存列表"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status_filter:
            cursor = await db.execute(
                "SELECT * FROM inventory WHERE status = ? ORDER BY checkin_time DESC",
                (status_filter,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM inventory ORDER BY checkin_time DESC"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
