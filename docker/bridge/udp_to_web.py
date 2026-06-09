#!/usr/bin/env python3
"""
SmartMonitor 真实数据桥接脚本

功能：监听 UDP 8080 接收 ESP32-P4 真实传感器 JSON，转发到 Web 仪表盘 API。
      与 pc_receiver.py 不冲突，可同时运行。

用法:
  python udp_to_web.py                          # 转发到 localhost:8000
  python udp_to_web.py --url http://x.x.x.x:8000 # 转发到远端仪表盘
  python udp_to_web.py --no-console              # 静默模式，不打印控制台输出

依赖: 仅标准库 + requests
"""

import socket
import json
import argparse
import sys
import datetime

# 可选依赖，首次运行时自动安装
try:
    import requests
except ImportError:
    print("[提示] 正在安装 requests...")
    import subprocess
    subprocess.check_call(
        [r"D:\Anaconda3\envs\ForAgents\Scripts\pip.exe", "install", "requests"]
    )
    import requests


# ==================== 配置常量 ====================
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080          # ESP32 发送目标端口
BUFFER_SIZE = 2048          # 单包 < 512 字节，留足余量
DEFAULT_WEB_URL = "http://localhost:8000"


class UdpToWebBridge:
    """UDP → HTTP 桥接器"""

    def __init__(self, web_url: str, silent: bool = False):
        self.api_endpoint = web_url.rstrip("/") + "/api/sensors"
        self.silent = silent
        self.sock: socket.socket | None = None
        self.running = True
        self.count = 0
        self.errors = 0

    def start(self) -> None:
        """启动桥接主循环"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((LISTEN_HOST, LISTEN_PORT))
        self.sock.settimeout(1.0)  # 1 秒超时，便于优雅退出

        self._print_banner()

        try:
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(BUFFER_SIZE)
                    self._handle_packet(data, addr)
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        print(f"[错误] socket 异常: {e}")
                    break
        except KeyboardInterrupt:
            print("\n正在关闭...")
        finally:
            self._cleanup()

    def stop(self) -> None:
        self.running = False

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """解析 UDP 包 → 转发到 Web API"""
        # 解码 UTF-8
        try:
            raw_str = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            if not self.silent:
                print(f"[{self._now()}] [跳过] 非 UTF-8 数据 ({len(data)} bytes) from {addr[0]}")
            return

        if not raw_str:
            return

        # JSON 解析
        try:
            obj = json.loads(raw_str)
        except json.JSONDecodeError as e:
            if not self.silent:
                print(f"[{self._now()}] [跳过] JSON 解析失败: {e}")
            return

        # 后台类型消息（如 checkin）也一并转发，仪表盘会自动忽略
        # 转发到 Web API
        try:
            resp = requests.post(self.api_endpoint, json=obj, timeout=5)
            self.count += 1
            if not self.silent:
                level = obj.get("level", 0)
                level_label = {0: "L0 OK", 1: "L1 YuJing", 2: "L2 YanZhong", 3: "L3 JinJi"}.get(level, f"L{level}")
                status = "✓" if resp.ok else f"✗ {resp.status_code}"
                dht11_t = obj.get("dht11_t", "?")
                dht11_h = obj.get("dht11_h", "?")
                mq135_v = obj.get("mq135_v", "?")
                alert = "🔴" if obj.get("alert") else ""
                print(f"  [{self.count:04d}] {status}  {level_label}  "
                      f"T={dht11_t}°C H={dht11_h}%  MQ={mq135_v}V  {alert}")
        except requests.ConnectionError:
            self.errors += 1
            if not self.silent:
                print(f"  [{self.count:04d}] ✗ 连接 Web 仪表盘失败，请确认服务已启动")
        except requests.Timeout:
            self.errors += 1
            if not self.silent:
                print(f"  [{self.count:04d}] ✗ 请求超时")
        except Exception as e:
            self.errors += 1
            if not self.silent:
                print(f"  [{self.count:04d}] ✗ 错误: {e}")

    def _print_banner(self) -> None:
        print("=" * 60)
        print("  SmartMonitor 真实数据桥接 (UDP:8080 → Web API)")
        print(f"  监听: UDP 0.0.0.0:{LISTEN_PORT}")
        print(f"  转发: POST {self.api_endpoint}")
        print("  等待 ESP32-P4 数据... (Ctrl+C 退出)")
        print("=" * 60)
        print()

    def _cleanup(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        print(f"\n[{self._now()}] 已停止。转发 {self.count} 条, 失败 {self.errors} 条。")

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description="SmartMonitor UDP→Web 真实数据桥接")
    parser.add_argument("--url", default=DEFAULT_WEB_URL,
                        help=f"仪表盘 API 地址 (默认: {DEFAULT_WEB_URL})")
    parser.add_argument("--no-console", action="store_true",
                        help="静默模式，不打印每条数据")
    args = parser.parse_args()

    bridge = UdpToWebBridge(web_url=args.url, silent=args.no_console)

    # Ctrl+C 信号处理
    import signal
    def handler(sig, frame):
        bridge.stop()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    bridge.start()


if __name__ == "__main__":
    main()
