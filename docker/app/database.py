"""
SQLite 数据库操作层 — 异步（aiosqlite）

表:
  - sensor_data:  传感器时间序列
  - inventory:    物料库存（复用 warehouse_db.py 结构）
  - check_log:    出入库流水（复用 warehouse_db.py 结构）
"""
import aiosqlite
import base64
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
                mq135_raw  INTEGER,
                light_raw  INTEGER,
                mq135_do  INTEGER DEFAULT -1,
                photo_do  INTEGER DEFAULT -1,
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
        # 报警事件表（含快照 JPEG BLOB）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alarm_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT NOT NULL,
                level        INTEGER NOT NULL,
                reason       TEXT DEFAULT '',
                dht11_t      REAL,
                dht11_h      REAL,
                ds18b20_t    REAL,
                mq135_v      REAL,
                mq135_raw     INTEGER,
                mq135_do     INTEGER DEFAULT -1,
                light_raw      INTEGER,
                photo_do     INTEGER DEFAULT -1,
                snapshot     BLOB DEFAULT NULL,
                acknowledged INTEGER DEFAULT 0
            )
        """)
        # ── 数据库迁移：为旧 alarm_events 表补充缺失列 ──
        for col, col_def in [
            ("ts", "TEXT NOT NULL DEFAULT ''"),
            ("snapshot", "BLOB DEFAULT NULL"),
            ("acknowledged", "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE alarm_events ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # 列已存在
        # ── 数据库迁移：sensor_data 和 alarm_events 补充 mq135_raw 列 ──
        for tbl in ["sensor_data", "alarm_events"]:
            try:
                await db.execute(f"ALTER TABLE {tbl} ADD COLUMN mq135_raw INTEGER")
            except Exception:
                pass
        await db.commit()
        # ── 报警配置表（v3.5 新增）──
        await init_alarm_config_table()


async def insert_sensor_data(data: dict) -> int:
    """插入一条传感器记录，返回 id"""
    _ensure_dir()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            INSERT INTO sensor_data (ts, dht11_t, dht11_h, ds18b20_t, mq135_v, mq135_raw, light_raw, mq135_do, photo_do, level, alert, reason, err)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            data.get("dht11_t"), data.get("dht11_h"),
            data.get("ds18b20_t"), data.get("mq135_v"),
            data.get("mq135_raw"),
            data.get("light_raw"),
            data.get("mq135_do", -1), data.get("photo_do", -1),
            data.get("level", 0),
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


# ==================== 异步入出库操作 ====================

async def do_checkin(item: dict, env: dict) -> bool:
    """
    执行入库操作（异步版，复用 warehouse_db.checkin 逻辑）

    Args:
        item: 物料信息 {id, name, category, batch, spec, mfg_date, exp_date}
        env:  环境快照 {temp, humi, level}

    Returns:
        True=成功(新入库或重新入库), False=重复入库(已在库)
    """
    _ensure_dir()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 检查是否已存在
        cursor = await db.execute(
            "SELECT id, status FROM inventory WHERE id = ?", (item["id"],)
        )
        existing = await cursor.fetchone()

        if existing and dict(existing)["status"] == "在库":
            return False  # 阻止重复入库

        env_temp = env.get("temp", 0)
        env_humi = env.get("humi", 0)

        if existing and dict(existing)["status"] == "已出库":
            # 重新入库：更新字段
            await db.execute("""
                UPDATE inventory SET
                    name=?, category=?, batch=?, spec=?, mfg_date=?,
                    exp_date=?, checkin_time=?, checkin_temp=?,
                    checkin_humi=?, status='在库'
                WHERE id=?
            """, (
                item.get("name", ""), item.get("category", ""),
                item.get("batch", ""), item.get("spec", ""),
                item.get("mfg_date", ""), item.get("exp_date", ""),
                now, env_temp, env_humi, item["id"]
            ))
        else:
            # 首次入库
            await db.execute("""
                INSERT INTO inventory
                    (id, name, category, batch, spec, mfg_date, exp_date,
                     checkin_time, checkin_temp, checkin_humi, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '在库')
            """, (
                item["id"], item.get("name", ""), item.get("category", ""),
                item.get("batch", ""), item.get("spec", ""),
                item.get("mfg_date", ""), item.get("exp_date", ""),
                now, env_temp, env_humi
            ))

        # 写入流水日志
        await db.execute("""
            INSERT INTO check_log (item_id, action, timestamp, env_temp, env_humi, env_level)
            VALUES (?, '入库', ?, ?, ?, ?)
        """, (item["id"], now, env_temp, env_humi, env.get("level", 0)))

        await db.commit()
        return True


async def do_checkout(item_id: str, env: dict) -> tuple[bool, dict | str]:
    """
    执行出库操作（异步版）

    Returns:
        (True, item_dict)  出库成功
        (False, error_msg) 失败原因
    """
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM inventory WHERE id = ?", (item_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            return False, f"物料 {item_id} 不在库存中"

        row_dict = dict(row)
        if row_dict["status"] == "已出库":
            return False, f"物料 {item_id} 已经出库过"

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        await db.execute(
            "UPDATE inventory SET status = '已出库' WHERE id = ?", (item_id,)
        )

        await db.execute("""
            INSERT INTO check_log (item_id, action, timestamp, env_temp, env_humi, env_level)
            VALUES (?, '出库', ?, ?, ?, ?)
        """, (item_id, now, env.get("temp", 0), env.get("humi", 0), env.get("level", 0)))

        await db.commit()
        return True, row_dict


async def get_check_log(limit: int = 20) -> list[dict]:
    """获取最近 N 条出入库流水记录"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM check_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def clear_inventory() -> int:
    """一键清除全部库存：清空 inventory 和 check_log 两张表，返回删除的库存记录数"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM inventory")
        count = (await cursor.fetchone())[0]
        await db.execute("DELETE FROM inventory")
        await db.execute("DELETE FROM check_log")
        await db.commit()
        return count


async def clear_check_log() -> int:
    """清空全部出入库流水记录，返回删除条数"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM check_log")
        await db.commit()
        return cursor.rowcount


async def clear_alarm_events() -> int:
    """清空全部报警事件记录，返回删除条数"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM alarm_events")
        await db.commit()
        return cursor.rowcount


async def delete_inventory_item(item_id: str) -> bool:
    """删除库存中指定物料（同时删关联流水，事务保证原子性），返回是否成功"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN")
        try:
            cursor = await db.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
            await db.execute("DELETE FROM check_log WHERE item_id = ?", (item_id,))
            await db.commit()
            return cursor.rowcount > 0
        except Exception:
            await db.rollback()
            raise


async def add_inventory_item(item: dict) -> tuple[bool, str]:
    """
    手动新增库存物料
    Returns: (True/False, message)
    """
    _ensure_dir()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 检查是否已存在
        cursor = await db.execute(
            "SELECT id FROM inventory WHERE id = ?", (item["id"],)
        )
        if await cursor.fetchone():
            return False, f"物料 {item['id']} 已存在，请使用不同 ID"
        await db.execute("""
            INSERT INTO inventory
                (id, name, category, batch, spec, mfg_date, exp_date,
                 checkin_time, checkin_temp, checkin_humi, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '在库')
        """, (
            item["id"], item.get("name", ""), item.get("category", ""),
            item.get("batch", ""), item.get("spec", ""),
            item.get("mfg_date", ""), item.get("exp_date", ""),
            now, item.get("checkin_temp", 0), item.get("checkin_humi", 0)
        ))
        await db.execute("""
            INSERT INTO check_log (item_id, action, timestamp, env_temp, env_humi, env_level)
            VALUES (?, '入库', ?, ?, ?, ?)
        """, (item["id"], now, item.get("checkin_temp", 0), item.get("checkin_humi", 0), 0))
        await db.commit()
        return True, f"手动新增成功: {item['id']}"


async def get_inventory_stats() -> list[dict]:
    """按分类统计库存：总计/在库数量"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT category,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='在库' THEN 1 ELSE 0 END) as in_stock
            FROM inventory
            GROUP BY category
            ORDER BY category
        """)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_latest_env_snapshot() -> dict | None:
    """
    从最新一条 sensor_data 获取当前环境快照。
    用于扫码时自动填充温度/湿度/级别。
    """
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT ds18b20_t, dht11_h, level FROM sensor_data ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        r = dict(row)
        return {
            "temp": r.get("ds18b20_t"),
            "humi": r.get("dht11_h"),
            "level": r.get("level", 0),
        }


# ==================== 报警事件操作 ====================

async def insert_alarm_event(
    level: int, reason: str = "",
    dht11_t: float | None = None, dht11_h: float | None = None,
    ds18b20_t: float | None = None, mq135_v: float | None = None,
    mq135_do: int | None = None, light_raw: float | None = None,
    photo_do: int | None = None, snapshot: bytes | None = None
) -> int:
    """插入一条报警事件记录（含快照），返回 id"""
    _ensure_dir()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            INSERT INTO alarm_events (ts, level, reason, dht11_t, dht11_h, ds18b20_t,
                                       mq135_v, mq135_do, light_raw, photo_do, snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, level, reason, dht11_t, dht11_h, ds18b20_t,
              mq135_v, mq135_do, light_raw, photo_do, snapshot))
        await db.commit()
        return cursor.lastrowid


async def get_alarm_events(page: int = 1, page_size: int = 20,
                           level_filter: int | None = None) -> tuple[list[dict], int]:
    """分页查询报警事件，可选按 level 筛选。返回 (items, total)"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        if level_filter is not None and level_filter > 0:
            count_cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM alarm_events WHERE level = ?",
                (level_filter,)
            )
            total = (await count_cursor.fetchone())["cnt"]
            offset = (page - 1) * page_size
            cursor = await db.execute(
                "SELECT id, ts, level, reason, dht11_t, dht11_h, ds18b20_t, "
                "mq135_v, mq135_do, light_raw, photo_do, "
                "CASE WHEN snapshot IS NOT NULL THEN 1 ELSE 0 END as has_snapshot, "
                "acknowledged "
                "FROM alarm_events WHERE level = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (level_filter, page_size, offset)
            )
        else:
            count_cursor = await db.execute("SELECT COUNT(*) as cnt FROM alarm_events")
            total = (await count_cursor.fetchone())["cnt"]
            offset = (page - 1) * page_size
            cursor = await db.execute(
                "SELECT id, ts, level, reason, dht11_t, dht11_h, ds18b20_t, "
                "mq135_v, mq135_do, light_raw, photo_do, "
                "CASE WHEN snapshot IS NOT NULL THEN 1 ELSE 0 END as has_snapshot, "
                "acknowledged "
                "FROM alarm_events ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, offset)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total


async def get_alarm_event_detail(alarm_id: int) -> dict | None:
    """获取单条报警事件详情（含快照 JPEG）"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM alarm_events WHERE id = ?", (alarm_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        r = dict(row)
        # 将 snapshot BLOB 转为 base64
        if r.get("snapshot"):
            r["snapshot_b64"] = base64.b64encode(r["snapshot"]).decode("ascii")
        else:
            r["snapshot_b64"] = None
        r.pop("snapshot", None)
        r["has_snapshot"] = bool(r.get("snapshot_b64"))
        return r


async def get_alarm_summary() -> dict:
    """获取报警概要：总数、未确认数、最新级别"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM alarm_events")
        total = (await cursor.fetchone())["cnt"]
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM alarm_events WHERE acknowledged = 0"
        )
        unack = (await cursor.fetchone())["cnt"]
        cursor = await db.execute(
            "SELECT level, reason, ts FROM alarm_events ORDER BY id DESC LIMIT 1"
        )
        latest = await cursor.fetchone()
        return {
            "total_count": total,
            "unacknowledged": unack,
            "latest_level": latest["level"] if latest else 0,
            "latest_reason": latest["reason"] if latest else "",
        }


async def acknowledge_alarm(alarm_id: int) -> bool:
    """确认一条报警"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "UPDATE alarm_events SET acknowledged = 1 WHERE id = ?", (alarm_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ==================== 报警配置操作 ====================

async def init_alarm_config_table():
    """初始化 alarm_config 表（在 init_db 中调用）"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alarm_config (
                id                  INTEGER PRIMARY KEY CHECK (id = 1),
                mq135_alarm_src     INTEGER DEFAULT 0,
                photo_alarm_src     INTEGER DEFAULT 0,
                mq135_ao_dir        INTEGER DEFAULT 0,
                photo_ao_dir        INTEGER DEFAULT 1,
                mq135_ao_threshold  INTEGER DEFAULT 3100,
                photo_ao_threshold  INTEGER DEFAULT 1000,
                dht11_temp_high     INTEGER DEFAULT 35,
                dht11_humi_high     INTEGER DEFAULT 85,
                ds18b20_temp_high   REAL DEFAULT 35.0,
                temp_humi_alarm_enabled INTEGER DEFAULT 1,
                updated_at          TEXT DEFAULT ''
            )
        """)
        # 兼容旧表: 列可能不存在时自动补齐
        try:
            await db.execute("ALTER TABLE alarm_config ADD COLUMN temp_humi_alarm_enabled INTEGER DEFAULT 1")
        except Exception:
            pass  # 列已存在
        # 确保存在唯一的配置行 (id=1)
        await db.execute("""
            INSERT OR IGNORE INTO alarm_config (id) VALUES (1)
        """)
        await db.commit()


async def get_alarm_config() -> dict:
    """获取当前报警配置"""
    _ensure_dir()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM alarm_config WHERE id = 1")
        row = await cursor.fetchone()
        if row is None:
            return {
                "mq135_alarm_src": 0, "photo_alarm_src": 0,
                "mq135_ao_dir": 0, "photo_ao_dir": 1,
                "mq135_ao_threshold": 3100, "photo_ao_threshold": 1000,
                "dht11_temp_high": 35, "dht11_humi_high": 85,
                "ds18b20_temp_high": 35.0, "temp_humi_alarm_enabled": 1,
                "updated_at": "",
            }
        return dict(row)


async def set_alarm_config(config: dict) -> dict:
    """写入报警配置（UPSERT id=1）"""
    _ensure_dir()
    import datetime as _dt
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("""
            INSERT INTO alarm_config (id, mq135_alarm_src, photo_alarm_src,
                mq135_ao_dir, photo_ao_dir, mq135_ao_threshold,
                photo_ao_threshold, dht11_temp_high, dht11_humi_high,
                ds18b20_temp_high, temp_humi_alarm_enabled, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                mq135_alarm_src        = excluded.mq135_alarm_src,
                photo_alarm_src        = excluded.photo_alarm_src,
                mq135_ao_dir           = excluded.mq135_ao_dir,
                photo_ao_dir           = excluded.photo_ao_dir,
                mq135_ao_threshold     = excluded.mq135_ao_threshold,
                photo_ao_threshold     = excluded.photo_ao_threshold,
                dht11_temp_high        = excluded.dht11_temp_high,
                dht11_humi_high        = excluded.dht11_humi_high,
                ds18b20_temp_high      = excluded.ds18b20_temp_high,
                temp_humi_alarm_enabled = excluded.temp_humi_alarm_enabled,
                updated_at             = excluded.updated_at
        """, (
            config.get("mq135_alarm_src", 0),
            config.get("photo_alarm_src", 0),
            config.get("mq135_ao_dir", 0),
            config.get("photo_ao_dir", 1),
            config.get("mq135_ao_threshold", 3100),
            config.get("photo_ao_threshold", 1000),
            config.get("dht11_temp_high", 35),
            config.get("dht11_humi_high", 85),
            config.get("ds18b20_temp_high", 35.0),
            config.get("temp_humi_alarm_enabled", 1),
            now,
        ))
        await db.commit()
        return await get_alarm_config()
