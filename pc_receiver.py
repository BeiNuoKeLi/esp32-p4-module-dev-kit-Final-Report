#!/usr/bin/env python3
"""
智能环境监测系统 - 上位机 UDP 接收脚本

功能（REQUIREMENT.md 第7节）：
  1. 监听 UDP 0.0.0.0:8080，接收 ESP32-P4 上传的 JSON 传感器数据
  2. 解析 JSON 报文（格式见 REQUIREMENT.md 6.2）
  3. 控制台带本地时间戳格式化输出
  4. alert==1 时红色高亮报警
  5. 追加写入 sensor_log.csv，首次运行时写入表头
  6. Ctrl+C 优雅退出

通信协议（REQUIREMENT.md 6.1）：
  - 目标端口: 8080
  - 传输方式: UDP (socket.SOCK_DGRAM)
  - 单包最大: < 512 字节

JSON 报文格式 v3.0（分级报警 + raw统一）：
{
  "type": "data",             // NEW: 消息类型 (data/checkin/alert_image)
  "level": 0,                 // NEW: 报警级别 (0=正常,1=预警,2=严重,3=紧急)
  "ts": 120000,               // FreeRTOS 启动后毫秒时间戳
  "dht11_t": 26.0,            // DHT11 温度 (°C)
  "dht11_h": 62.0,            // DHT11 湿度 (%RH)
  "ds18b20_t": 28.3125,       // DS18B20 温度 (°C, 4位小数)
  "mq135_v": 1.25,            // MQ-135 AO 电压 (V), 向后兼容
  "mq135_raw": 1551,          // MQ-135 ADC 原始值 (0~4095), 与光敏统一
  "light_raw": 1500,          // 光敏 ADC 原始值 (0~4095)
  "alert": 0,                 // 0=正常, 1=任一报警源触发
  "err": 0,                   // 错误位掩码
  "reason": ""                // 报警原因 (mq135/dht11_temp/ds18b20_temp/photo/dht11_humi)
}

报警源分类:
  A类(风机关联): MQ-135 DO=0 / DHT11温度≥38°C / DS18B20温度≥38°C
  B类(仅提醒):   光敏 DO=0 / DHT11湿度≥85%
  
显示颜色:
  L0 正常: 绿色
  L1 预警: 黄色
  L2 严重: 橙色
  L3 紧急: 红色

仅使用标准库，无需 pip install（REQUIREMENT.md 7.3）
"""

import socket
import json
import datetime
import csv
import os
import signal
import sys


# ==================== 配置常量 ====================
# 监听参数（REQUIREMENT.md 7.1: 绑定 0.0.0.0:8080）
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080

# CSV 日志文件
CSV_FILE = "sensor_log.csv"

# 接收缓冲区大小（REQUIREMENT.md 6.1: 单包 < 512 字节，取 2048 留足余量）
BUFFER_SIZE = 2048


class UdpReceiver:
    """UDP 接收器：监听 + 解析 + 显示 + 日志"""

    def __init__(self, host: str = LISTEN_HOST, port: int = LISTEN_PORT):
        """
        初始化 UDP socket

        API 参考: Python socket 标准库
          socket.socket(family, type) → socket 对象
          socket.bind((host, port))  → 绑定地址端口
        """
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self.csv_written_header = os.path.exists(CSV_FILE)
        self.running = True

    def start(self) -> None:
        """启动 UDP 监听主循环"""
        # ---- 1. 创建 UDP socket ----
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 允许端口复用，避免重启时 "Address in use" 错误
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))

        self._print_banner()

        try:
            while self.running:
                try:
                    # ---- 2. 接收数据 ----
                    # socket.recvfrom(bufsize) → (bytes, address)
                    data, addr = self.sock.recvfrom(BUFFER_SIZE)
                    self._handle_packet(data, addr)

                except socket.timeout:
                    # 超时后继续循环，检查 running 标志
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
        """停止接收器（由信号触发）"""
        self.running = False

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """
        处理收到的 UDP 数据包

        流程（REQUIREMENT.md 7.1）：
          1. 解码为 UTF-8 字符串
          2. json.loads() 解析
          3. 控制台格式化输出（带本地时间戳）
          4. 报警高亮（alert==1 时红色）
          5. 追加写入 CSV 日志
        """
        # ---- 1. 解码 ----
        try:
            raw_str = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            print(f"[{self._now()}] [警告] 收到非 UTF-8 数据 ({len(data)} bytes), 来自 {addr[0]}:{addr[1]}")
            return

        if not raw_str:
            return

        # ---- 2. JSON 解析 ----
        try:
            obj = json.loads(raw_str)
        except json.JSONDecodeError as e:
            print(f"[{self._now()}] [警告] JSON 解析失败: {e} | raw={raw_str[:120]}")
            return

        # ---- 3. 控制台输出 ----
        self._print_data(obj)

        # ---- 4. CSV 日志 ----
        self._write_csv(obj)

    def _print_data(self, obj: dict) -> None:
        """
        控制台格式化输出 (v2.0 分级报警)

        按报警级别显示不同颜色:
          L0 正常: 绿色
          L1 预警: 黄色
          L2 严重: 橙色 (亮红)
          L3 紧急: 红色 + 闪烁前缀
        """
        ts = self._now()

        # 安全提取字段（容错：字段可能缺失）
        msg_type = obj.get("type", "?")
        level = obj.get("level", 0)
        dht11_t = obj.get("dht11_t", 0)
        dht11_h = obj.get("dht11_h", 0)
        ds18b20_t = obj.get("ds18b20_t", 0)
        mq135_v = obj.get("mq135_v", 0)
        mq135_raw = obj.get("mq135_raw", 0)
        light_raw = obj.get("light_raw", 0)
        alert = obj.get("alert", 0)
        err = obj.get("err", 0)
        reason = obj.get("reason", "")
        esp_ts = obj.get("ts", 0)

        # 按级别映射颜色和标签
        level_label = {0: "L0 OK", 1: "L1 YuJing", 2: "L2 YanZhong", 3: "L3 JinJi"}.get(level, f"L?({level})")
        level_color = {
            0: "\033[92m",   # 绿色  L0 正常
            1: "\033[93m",   # 黄色  L1 预警
            2: "\033[38;5;208m",  # 橙色  L2 严重
            3: "\033[91m",   # 红色  L3 紧急
        }.get(level, "\033[0m")

        # 构造报警状态字符串
        alert_str = f"{level_label}"
        if alert == 1:
            reason_str = f" ({reason})" if reason else ""

        # 构造错误码字符串
        err_field = f"0x{err:02X}"

        # 格式化输出
        line = (f"[{ts}] "
                f"DHT11: {dht11_t:>5.1f}°C | {dht11_h:>5.1f}% "
                f"|| DS18B20: {ds18b20_t:>8.4f}°C "
                f"|| MQ135: {mq135_raw} raw ({mq135_v:.2f}V) | Light: {light_raw} raw "
                f"|| {level_color}{alert_str:>14}\033[0m | Err: {err_field}")

        if level >= 3:
            print(f"\033[91m[紧急] {line}\033[0m")
        elif level >= 2:
            print(line)
        elif level >= 1:
            print(line)
        else:
            print(line)

    def _write_csv(self, obj: dict) -> None:
        """
        追加写入 CSV 日志文件 (v2.0 分级报警)

        首次写入时自动写表头
        CSV 列: timestamp, esp_ts, type, level, dht11_t, dht11_h, ds18b20_t, mq135_v, light_raw, alert, err, reason
        """
        try:
            with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                # 首次写入表头
                if not self.csv_written_header:
                    writer.writerow([
                        "timestamp", "esp_ts",
                        "type", "level",
                        "dht11_t", "dht11_h",
                        "ds18b20_t",
                        "mq135_v", "mq135_raw", "light_raw",
                        "alert", "err", "reason"
                    ])
                    self.csv_written_header = True

                # 写入数据行
                writer.writerow([
                    datetime.datetime.now().isoformat(),
                    obj.get("ts", 0),
                    obj.get("type", ""),
                    obj.get("level", 0),
                    obj.get("dht11_t", ""),
                    obj.get("dht11_h", ""),
                    obj.get("ds18b20_t", ""),
                    obj.get("mq135_v", ""),
                    obj.get("mq135_raw", ""),
                    obj.get("light_raw", ""),
                    obj.get("alert", ""),
                    f"0x{obj.get('err', 0):02X}",
                    obj.get("reason", "")
                ])
        except OSError as e:
            print(f"[{self._now()}] [错误] CSV 写入失败: {e}")

    def _print_banner(self) -> None:
        """打印启动横幅"""
        print("=" * 60)
        print("  智慧化工仓储环境监测系统 - 上位机接收端 v2.0")
        print(f"  监听端口: {self.port}")
        print(f"  日志文件: {CSV_FILE}")
        print("  报警级别: L0 正常 | L1 预警 | L2 严重 | L3 紧急")
        print("=" * 60)
        print("  等待 ESP32-P4 数据... (Ctrl+C 退出)\n")

    def _cleanup(self) -> None:
        """清理资源：关闭 socket"""
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        print(f"\n[{self._now()}] 接收器已关闭。")

    @staticmethod
    def _now() -> str:
        """
        获取当前本地时间戳字符串

        格式（REQUIREMENT.md 7.1）:
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        """
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 入口 ====================
def main() -> None:
    """主入口：创建接收器并启动"""
    receiver = UdpReceiver()

    # 注册信号处理（支持 Ctrl+C 以外的方式终止）
    def signal_handler(sig, frame):
        print(f"\n收到信号 {sig}，正在关闭...")
        receiver.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动监听
    receiver.start()


if __name__ == "__main__":
    main()
