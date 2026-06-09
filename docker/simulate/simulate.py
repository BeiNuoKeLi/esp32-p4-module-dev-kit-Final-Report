#!/usr/bin/env python3
"""
SmartMonitor 模拟数据注入脚本

脱离 Docker 独立运行，定时 POST 模拟传感器 JSON 到仪表盘 API。
用于无 ESP32-P4 硬件时的功能验证和演示。

用法:
  python simulate.py                        # 默认 2s 间隔, POST 到 localhost:8000
  python simulate.py --interval 5           # 5 秒间隔
  python simulate.py --url http://x.x.x.x:8000  # 指定远端地址

依赖: 仅标准库 + requests（预装环境已有）
"""
import json
import random
import time
import argparse
import sys

try:
    import requests
except ImportError:
    print("[错误] 缺少 requests 库，正在安装...")
    import subprocess
    subprocess.check_call(
        [r"D:\Anaconda3\envs\ForAgents\Scripts\pip.exe", "install", "requests"]
    )
    import requests


def generate_sensor_data(level_override: int | None = None) -> dict:
    """
    生成模拟传感器数据

    正常模式 (80%): level=0, 各传感器值在正常范围内
    预警模式 (10%): level=1, 温度偏高
    严重模式 (7%):  level=2, 气体浓度升高
    紧急模式 (3%):  level=3, 高温+高气体+高湿度
    """
    roll = random.random()
    if level_override is not None:
        level = level_override
    elif roll < 0.03:
        level = 3
    elif roll < 0.10:
        level = 2
    elif roll < 0.20:
        level = 1
    else:
        level = 0

    if level == 0:
        dht11_t = round(random.uniform(22.0, 30.0), 1)
        dht11_h = round(random.uniform(40.0, 65.0), 1)
        ds18b20_t = round(random.uniform(22.0, 30.0), 4)
        mq135_v = round(random.uniform(0.3, 1.0), 3)
        light_v = round(random.uniform(0.5, 2.5), 3)
        alert = 0
        reason = ""
    elif level == 1:
        dht11_t = round(random.uniform(30.0, 36.0), 1)
        dht11_h = round(random.uniform(60.0, 75.0), 1)
        ds18b20_t = round(random.uniform(30.0, 36.0), 4)
        mq135_v = round(random.uniform(1.0, 1.8), 3)
        light_v = round(random.uniform(0.5, 2.0), 3)
        alert = 1
        reason = random.choice(["dht11_temp", "dht11_humi"])
    elif level == 2:
        dht11_t = round(random.uniform(35.0, 38.0), 1)
        dht11_h = round(random.uniform(70.0, 82.0), 1)
        ds18b20_t = round(random.uniform(35.0, 38.0), 4)
        mq135_v = round(random.uniform(2.0, 2.8), 3)
        light_v = round(random.uniform(0.3, 1.5), 3)
        alert = 1
        reason = random.choice(["mq135", "dht11_temp", "ds18b20_temp"])
    else:  # level == 3
        dht11_t = round(random.uniform(38.0, 45.0), 1)
        dht11_h = round(random.uniform(80.0, 95.0), 1)
        ds18b20_t = round(random.uniform(38.0, 48.0), 4)
        mq135_v = round(random.uniform(3.0, 4.5), 3)
        light_v = round(random.uniform(0.1, 0.8), 3)
        alert = 1
        reason = "mq135+dht11_temp+ds18b20_temp"

    err = 0
    if random.random() < 0.05:
        err = random.choice([0x01, 0x02, 0x03])

    return {
        "type": "data",
        "level": level,
        "ts": int(time.time() * 1000),
        "dht11_t": dht11_t,
        "dht11_h": dht11_h,
        "ds18b20_t": ds18b20_t,
        "mq135_v": mq135_v,
        "light_v": light_v,
        "alert": alert,
        "err": err,
        "reason": reason,
    }


def main():
    parser = argparse.ArgumentParser(description="SmartMonitor 模拟传感器数据注入")
    parser.add_argument(
        "--url", default="http://localhost:8000",
        help="仪表盘 API 地址 (默认: http://localhost:8000)"
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="发送间隔 / 秒 (默认: 2.0)"
    )
    parser.add_argument(
        "--level", type=int, default=None, choices=[0, 1, 2, 3],
        help="固定报警级别 (0=正常, 1=预警, 2=严重, 3=紧急)"
    )
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/api/sensors"
    print(f"📡 SmartMonitor 模拟数据注入")
    print(f"   目标: {endpoint}")
    print(f"   间隔: {args.interval}s")
    if args.level is not None:
        print(f"   固定级别: L{args.level}")
    print(f"   按 Ctrl+C 停止\n")

    count = 0
    try:
        while True:
            data = generate_sensor_data(args.level)
            try:
                resp = requests.post(endpoint, json=data, timeout=5)
                count += 1
                level_str = f"L{data['level']}"
                status = "✓" if resp.ok else f"✗ {resp.status_code}"
                print(f"  [{count:04d}] {status}  {level_str}  "
                      f"T={data['dht11_t']}°C H={data['dht11_h']}%  "
                      f"MQ={data['mq135_v']}V  "
                      + (f"报警: {data['reason']}" if data['alert'] else ""))
            except requests.ConnectionError:
                print(f"  [{count:04d}] ✗ 连接失败，请确认仪表盘服务已启动")
            except requests.Timeout:
                print(f"  [{count:04d}] ✗ 请求超时")
            except Exception as e:
                print(f"  [{count:04d}] ✗ 错误: {e}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n已停止，共发送 {count} 条数据。")


if __name__ == "__main__":
    main()
