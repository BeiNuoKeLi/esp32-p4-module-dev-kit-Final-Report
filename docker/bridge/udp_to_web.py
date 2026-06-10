#!/usr/bin/env python3
"""
SmartMonitor 数据桥接脚本 v2.0

功能：
  - 监听 UDP 8080，接收 ESP32-P4 真实传感器 JSON → 转发到 Web 仪表盘
  - 支持模拟模式（--simulate），无需 ESP32 即可测试仪表盘
  - 自动路由不同消息类型到对应的 API 端点

用法:
  真实模式（需要 ESP32）:
    python udp_to_web.py
    python udp_to_web.py --url http://192.168.1.100:8000

  模拟模式（无需硬件）:
    python udp_to_web.py --simulate
    python udp_to_web.py --simulate --interval 3.0

依赖: 仅标准库 + requests
"""

import socket
import json
import argparse
import sys
import time
import random
import datetime
import threading

# 自动安装依赖
try:
    import requests
except ImportError:
    print("[提示] 正在安装 requests...")
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "requests", "-q"]
    )
    import requests


# ==================== 配置 ====================
UDP_PORT = 8080
BUFFER_SIZE = 2048
DEFAULT_WEB_URL = "http://localhost:8000"
SIM_DEFAULT_INTERVAL = 5.0  # 模拟模式下发送间隔（秒）

# API 端点路由表: type字段 → (HTTP方法, 路径后缀)
ROUTE_TABLE = {
    "data":     ("POST", "/api/sensors"),
    "checkin":  ("POST", "/api/warehouse/checkin"),
    "checkout": ("POST", "/api/warehouse/checkout"),
}

# 模拟用的传感器基线值
SIM_BASE = {
    "dht11_t": 25.0, "dht11_h": 55.0,
    "ds18b20_t": 24.0, "mq135_v": 0.9,
    "light_v": 1.8,
}

# 模拟用的物料列表
SIM_ITEMS = [
    {"item_id": "SIM-A001", "name": "电阻10kΩ", "category": "电子元件",
     "batch": "B2024Q1", "spec": "0805 ±1%", "mfg_date": "2024-03-15", "exp_date": "2027-03-15"},
    {"item_id": "SIM-B002", "name": "电容100μF", "category": "电子元件",
     "batch": "C2024Q2", "spec": "16V 铝电解", "mfg_date": "2024-06-01", "exp_date": "2027-06-01"},
    {"item_id": "SIM-C003", "name": "温湿度探头", "category": "传感器",
     "batch": "S2024A", "spec": "DHT22 模块", "mfg_date": "2024-09-10", "exp_date": "2028-09-10"},
]


# ==================== 桥接器核心 ====================

class DataBridge:
    """UDP/模拟 → HTTP API 桥接器"""

    def __init__(self, web_url: str, simulate: bool = False,
                 interval: float = SIM_DEFAULT_INTERVAL, silent: bool = False):
        self.base_url = web_url.rstrip("/")
        self.simulate = simulate
        self.interval = interval
        self.silent = silent
        self.running = True
        self.sock: socket.socket | None = None

        # 统计
        self.stats: dict[str, int] = {}
        self.errors = 0
        self.sim_seq = 0

    # ── 主入口 ──

    def start(self):
        self._print_banner()
        self._health_check()

        if self.simulate:
            self._run_simulate()
        else:
            self._run_udp()

    # ── UDP 模式 ──

    def _run_udp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("0.0.0.0", UDP_PORT))
        except OSError as e:
            print(f"[错误] 无法绑定 UDP:{UDP_PORT} — {e}")
            return
        self.sock.settimeout(1.0)

        print(f"[UDP] 监听 0.0.0.0:{UDP_PORT}，等待 ESP32-P4 数据...\n")
        try:
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(BUFFER_SIZE)
                    self._handle_udp(data, addr)
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        print(f"[错误] socket: {e}")
                    break
        except KeyboardInterrupt:
            print("\n")
        finally:
            self._cleanup()

    def _handle_udp(self, data: bytes, addr: tuple):
        """解析 UDP JSON → 按 type 路由转发"""
        try:
            raw = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            if not self.silent:
                print(f"  [跳过] 非 UTF-8 ({len(data)}B) from {addr[0]}")
            return
        if not raw:
            return

        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            if not self.silent:
                print(f"  [跳过] JSON 解析失败 from {addr[0]}")
            return

        self._dispatch(obj, source=f"UDP:{addr[0]}")

    # ── 模拟模式 ──

    def _run_simulate(self):
        print(f"[模拟] 每 {self.interval}s 推送一条传感器数据...")
        print(f"[模拟] 按 Enter 可手动触发一次出入库模拟\n")
        sim_checkin = threading.Thread(target=self._sim_keyboard, daemon=True)
        sim_checkin.start()

        # 初始化 EMA 状态
        ema: dict[str, float] = {}
        for k, v in SIM_BASE.items():
            ema[k] = v

        while self.running:
            # 生成传感器数据（带随机波动 + EMA 平滑）
            alpha = 0.3
            sensor = {"type": "data", "level": 0, "alert": 0, "err": 0, "reason": ""}
            for key, base in SIM_BASE.items():
                noise = random.gauss(0, base * 0.02)  # 2% 噪声
                raw = base + noise
                ema[key] = alpha * raw + (1 - alpha) * ema[key]
                sensor[key] = round(ema[key], 2)

            # 偶尔触发报警
            if random.random() < 0.08:
                level = random.choice([1, 2])
                reasons = {1: "温度偏高", 2: "湿度超标"}
                sensor["level"] = level
                sensor["alert"] = 1
                sensor["reason"] = reasons.get(level, "异常")
            # 偶尔模拟传感器错误
            if random.random() < 0.03:
                sensor["err"] = random.choice([1, 2, 4])

            self._dispatch(sensor, source="SIM", silent_ok=True)

            time.sleep(self.interval)

        self._cleanup()

    def _sim_keyboard(self):
        """键盘线程：按 Enter 模拟一次出/入库"""
        import os as _os
        while self.running:
            try:
                input("")  # 等待 Enter
            except (EOFError, OSError):
                break
            if not self.running:
                break

            action = random.choice(["checkin", "checkout", "checkin"])
            item = random.choice(SIM_ITEMS)

            if action == "checkin":
                msg = {
                    "type": "checkin",
                    "item_id": item["item_id"],
                    "name": item["name"],
                    "category": item["category"],
                    "batch": item["batch"],
                    "spec": item["spec"],
                    "mfg_date": item["mfg_date"],
                    "exp_date": item["exp_date"],
                    "env_temp": round(SIM_BASE["dht11_t"] + random.uniform(-0.5, 0.5), 1),
                    "env_humi": round(SIM_BASE["dht11_h"] + random.uniform(-2, 2), 1),
                    "env_level": 0,
                }
            else:
                # 随机选一个在库的物料出库
                try:
                    resp = requests.get(f"{self.base_url}/api/inventory?status=在库", timeout=5)
                    in_stock = resp.json()
                    if in_stock:
                        target = random.choice(in_stock)
                        mid = target["id"]
                    else:
                        mid = item["item_id"]
                except Exception:
                    mid = item["item_id"]

                msg = {
                    "type": "checkout",
                    "item_id": mid,
                    "env_temp": round(SIM_BASE["dht11_t"] + random.uniform(-0.5, 0.5), 1),
                    "env_humi": round(SIM_BASE["dht11_h"] + random.uniform(-2, 2), 1),
                    "env_level": 0,
                }

            self._dispatch(msg, source="SIM-manual")

    # ── 核心：消息路由与转发 ──

    def _dispatch(self, obj: dict, source: str = "?", silent_ok: bool = False):
        """根据 type 字段路由到正确的 API 端点"""
        msg_type = obj.get("type", "data")

        if msg_type in ROUTE_TABLE:
            method, path = ROUTE_TABLE[msg_type]
        else:
            # 未知类型 → 默认按 sensor 处理
            method, path = "POST", "/api/sensors"
            msg_type = "data"

        url = f"{self.base_url}{path}"

        # 移除 type 字段再发送（FastAPI 不需要）
        payload = {k: v for k, v in obj.items()}

        try:
            if method == "POST":
                resp = requests.post(url, json=payload, timeout=5)
            else:
                resp = requests.get(url, timeout=5)

            self.stats[msg_type] = self.stats.get(msg_type, 0) + 1
            total = sum(self.stats.values())
            ok = resp.ok

            if not self.silent or not ok or not silent_ok:
                status = "✓" if ok else f"✗ {resp.status_code}"
                detail = self._format_log(msg_type, obj, resp)
                print(f"  [{total:04d}] {status} {msg_type:>8s} → {path:26s} {detail}")

        except requests.ConnectionError:
            self.errors += 1
            if not self.silent:
                print(f"  [错误] 无法连接 {self.base_url}，请确认 Docker 已启动")
        except requests.Timeout:
            self.errors += 1
            if not self.silent:
                print(f"  [错误] 请求超时: {url}")
        except Exception as e:
            self.errors += 1
            if not self.silent:
                print(f"  [错误] {e}")

    @staticmethod
    def _format_log(msg_type: str, obj: dict, resp) -> str:
        """生成可读的日志详情"""
        if msg_type == "data":
            t = obj.get("dht11_t", "?")
            h = obj.get("dht11_h", "?")
            lv = obj.get("level", 0)
            alert = "🔴" if obj.get("alert") else ""
            return f"T={t}°C H={h}% Lv={lv} {alert}"
        elif msg_type == "checkin":
            msg = resp.json().get("message", "") if resp.ok else ""
            return f"物料={obj.get('item_id','?')} {msg}"
        elif msg_type == "checkout":
            msg = resp.json().get("message", "") if resp.ok else ""
            return f"物料={obj.get('item_id','?')} {msg}"
        return ""

    # ── 辅助 ──

    def _health_check(self):
        """检查 Web 服务是否可达"""
        try:
            resp = requests.get(f"{self.base_url}/api/status", timeout=3)
            if resp.ok:
                print(f"[健康检查] ✅ {self.base_url} 在线")
            else:
                print(f"[健康检查] ⚠️ {self.base_url} 返回 {resp.status_code}")
        except requests.ConnectionError:
            print(f"[健康检查] ❌ {self.base_url} 无法连接！")
            print("  请先启动 Docker: cd docker && docker compose up -d")
            if not self.simulate:
                return
        except Exception as e:
            print(f"[健康检查] ⚠️ {e}")

    def _print_banner(self):
        mode = "模拟模式 (无 ESP32)" if self.simulate else "UDP 监听模式"
        print("=" * 60)
        print(f"  SmartMonitor 数据桥接 v2.0 — {mode}")
        print(f"  目标: {self.base_url}")
        if self.simulate:
            print(f"  模拟间隔: {self.interval}s | 按 Enter 触发出入库")
        else:
            print(f"  监听: UDP 0.0.0.0:{UDP_PORT}")
        print("=" * 60)

    def _cleanup(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] 已停止。")
        if self.stats:
            print("  统计:", ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())))
        if self.errors:
            print(f"  失败: {self.errors} 次")
        print()


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="SmartMonitor 数据桥接 — UDP/模拟 → Web API"
    )
    parser.add_argument("--url", default=DEFAULT_WEB_URL,
                        help=f"仪表盘地址 (默认: {DEFAULT_WEB_URL})")
    parser.add_argument("--simulate", action="store_true",
                        help="模拟模式：无需 ESP32，自动生成传感器数据")
    parser.add_argument("--interval", type=float, default=SIM_DEFAULT_INTERVAL,
                        help=f"模拟模式发送间隔秒数 (默认: {SIM_DEFAULT_INTERVAL})")
    parser.add_argument("--quiet", action="store_true",
                        help="静默模式，减少控制台输出")
    args = parser.parse_args()

    bridge = DataBridge(
        web_url=args.url,
        simulate=args.simulate,
        interval=args.interval,
        silent=args.quiet,
    )

    import signal
    def handler(sig, frame):
        bridge.running = False
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    bridge.start()


if __name__ == "__main__":
    main()
