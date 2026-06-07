#!/usr/bin/env python3
"""
危化品仓储管理 - SQLite 数据库操作模块
=========================================
提供 inventory（库存表）和 check_log（出入库流水表）的 CRUD 操作。

数据库文件: warehouse.db (SQLite3)
"""

import sqlite3
import os
import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "warehouse.db")


class WarehouseDB:
    """仓储数据库操作类"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    # ==================== 表初始化 ====================

    def _init_tables(self):
        """创建 inventory 和 check_log 表（如果不存在）"""
        self.conn.execute("""
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
        self.conn.execute("""
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
        self.conn.commit()

    # ==================== 入库操作 ====================

    def checkin(self, item: dict, env: dict) -> bool:
        """
        入库操作

        Args:
            item: 物料信息字典 {
                id, name, category, batch, spec, mfg_date, exp_date
            }
            env:  环境快照字典 {
                temp, humi, level
            }

        Returns:
            True  = 新入库成功
            False = 该物料已在库且状态为"在库"（重复入库阻止）
        """
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 检查是否已在库
        existing = self.conn.execute(
            "SELECT id, status FROM inventory WHERE id = ?", (item["id"],)
        ).fetchone()

        if existing and existing["status"] == "在库":
            return False  # 重复入库

        env_temp = env.get("temp", 0)
        env_humi = env.get("humi", 0)

        if existing and existing["status"] == "已出库":
            # 再次入库：更新 status
            self.conn.execute("""
                UPDATE inventory SET
                    name=?, category=?, batch=?, spec=?, mfg_date=?, exp_date=?,
                    checkin_time=?, checkin_temp=?, checkin_humi=?, status='在库'
                WHERE id=?
            """, (
                item.get("name", ""), item.get("category", ""),
                item.get("batch", ""), item.get("spec", ""),
                item.get("mfg_date", ""), item.get("exp_date", ""),
                now, env_temp, env_humi, item["id"]
            ))
        else:
            # 首次入库
            self.conn.execute("""
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

        # 写入入库日志
        self.conn.execute("""
            INSERT INTO check_log (item_id, action, timestamp, env_temp, env_humi, env_level)
            VALUES (?, '入库', ?, ?, ?, ?)
        """, (item["id"], now, env_temp, env_humi, env.get("level", 0)))

        self.conn.commit()
        return True

    # ==================== 出库操作 ====================

    def checkout(self, item_id: str, env: dict) -> tuple:
        """
        出库操作

        Args:
            item_id: 物料编号
            env:     环境快照 {temp, humi, level}

        Returns:
            (success: bool, item_info: dict | str)
            - True + dict: 出库成功，返回物料信息
            - False + str: 失败原因
        """
        row = self.conn.execute(
            "SELECT * FROM inventory WHERE id = ?", (item_id,)
        ).fetchone()

        if row is None:
            return False, f"物料 {item_id} 不在库存中"

        if row["status"] == "已出库":
            return False, f"物料 {item_id} 已经出库过"

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 更新状态
        self.conn.execute(
            "UPDATE inventory SET status = '已出库' WHERE id = ?", (item_id,)
        )

        # 写出库日志
        self.conn.execute("""
            INSERT INTO check_log (item_id, action, timestamp, env_temp, env_humi, env_level)
            VALUES (?, '出库', ?, ?, ?, ?)
        """, (item_id, now, env.get("temp", 0), env.get("humi", 0), env.get("level", 0)))

        self.conn.commit()

        return True, dict(row)

    # ==================== 查询操作 ====================

    def get_inventory(self, status_filter: str | None = None) -> list[dict]:
        """
        获取库存列表

        Args:
            status_filter: None=全部, '在库', '已出库'

        Returns:
            [dict, ...] 库存记录列表
        """
        if status_filter:
            rows = self.conn.execute(
                "SELECT * FROM inventory WHERE status = ? ORDER BY checkin_time DESC",
                (status_filter,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM inventory ORDER BY checkin_time DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_check_log(self, limit: int = 50) -> list[dict]:
        """获取最近出入库流水记录"""
        rows = self.conn.execute(
            "SELECT * FROM check_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_inventory_count(self, status_filter: str | None = None) -> int:
        """获取库存数量"""
        if status_filter:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM inventory WHERE status = ?",
                (status_filter,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM inventory"
            ).fetchone()
        return row["cnt"] if row else 0

    # ==================== 清理 ====================

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()


# ==================== 自测入口 ====================
if __name__ == "__main__":
    db = WarehouseDB()

    # 测试入库
    test_item = {
        "id": "CHEM-20260607-001",
        "name": "工业酒精",
        "category": "易燃液体",
        "batch": "B2026-0501",
        "spec": "500ml/瓶",
        "mfg_date": "2026-05-01",
        "exp_date": "2027-05-01",
    }
    test_env = {"temp": 25.0, "humi": 62.0, "level": 0}

    result = db.checkin(test_item, test_env)
    print(f"入库结果: {'成功' if result else '重复'}")

    # 查看库存
    inventory = db.get_inventory()
    print(f"\n当前库存 ({len(inventory)} 条):")
    for item in inventory:
        print(f"  {item['id']} | {item['name']} | {item['status']} | {item['checkin_time']}")

    # 查看日志
    logs = db.get_check_log(5)
    print(f"\n最近 {len(logs)} 条操作日志:")
    for log in logs:
        print(f"  {log['timestamp']} | {log['item_id']} | {log['action']}")

    db.close()
