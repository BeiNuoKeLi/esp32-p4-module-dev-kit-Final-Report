# 智能环境监测系统

基于 **ESP32-P4** (RISC-V) + **ESP-IDF v5.5.1** 的多传感器环境监测终端，通过 OLED 实时显示数据，支持声光报警。

## 硬件接线

| 传感器 | 信号 | GPIO | 说明 |
|--------|------|------|------|
| DHT11 | DATA | 2 | 温湿度 |
| DS18B20 | DATA | 1 | 高精度温度 |
| MQ-135 | AO / DO | 21 / 22 | 空气质量 |
| 光敏电阻 | AO / DO | 20 / 23 | 光照强度 |
| 蜂鸣器 | CTRL | 25 | S8050 驱动有源蜂鸣器 |
| LED（红色） | 阳极 | 26 | 共阴极双色LED，报警状态点亮 |
| LED（绿色） | 阳极 | 27 | 共阴极双色LED，正常状态点亮 |
| 继电器 | IN | 32 | 高电平闭合，正常运转/报警停止 |
| OLED SSD1306 | SDA / SCL | 7 / 8 | 0.96" I2C 128×64 |

## 构建 & 烧录

```bash
idf.py build flash monitor
```

## OLED 显示

| 行 | 内容 | 示例 |
|----|------|------|
| 0 | 标题 | `Smart Monitor` |
| 1 | DHT11 温湿度 | `T=26C H=62%` |
| 2 | DS18B20 温度 | `28.3125 C` |
| 3 | MQ-135 电压+状态 | `1.25V OK` |
| 4 | 光敏 ADC 值+状态 | `2048raw OK` |
| 5 | 报警汇总+错误码 | `Alrt:OFF Err:0x00` |
| 6 | 蜂鸣器+WiFi状态 | `Buz:OFF  WiFi:OK` |
| 7 | 继电器+LED状态 | `Fan:ON LED:GRN` |

- 每 **1 秒** 刷新，报警时蜂鸣器间歇鸣叫（100ms 鸣 / 500ms 停）

## 软件架构

```
4 × 传感器任务 (prio 3) ──→ sensor_shared_t ←── OLED 显示任务 (prio 2)
                                  ↓                   Buzzer 报警任务 (prio 2)
                            g_sensor_mutex            LED 控制 + 继电器控制 (集成在 Buzzer 任务)
                                  ↓                   UDP 发送任务 (prio 2, Core 1)
                         udp_sender.c → 10.16.234.215:8080
                                  ↓
                         pc_receiver.py (Windows 上位机)

ESP32-CAM ──HTTP──→ camera_http_fetch.c ──UDP:8082──→ camera_display_receiver.py
                           (prio 1)                    (Windows 上位机)
```

- **`sensors.c/h`**：DHT11 / DS18B20 / MQ-135 / 光敏 / 蜂鸣器 / LED / 继电器 驱动
- **`oled_ssd1306.c/h`**：SSD1306 I2C 驱动，Page Addressing 逐页刷新
- **`udp_sender.c/h`**：JSON 组包 + UDP Socket 发送 (snprintf, lwip/sockets.h)，带 ENOMEM 退避重试
- **`camera_http_fetch.c/h`**：HTTP 拉取 ESP32-CAM JPEG → 0xAA55 协议 UDP 分包转发
- **`smart_monitor_main.c`**：主入口，Wi-Fi STA + 8 个 FreeRTOS 任务创建（LED/继电器集成在 buzzer 任务中）
- **`pc_receiver.py`**：Windows 上位机 UDP 接收脚本 (监听 8080, CSV 日志)
- **`camera_display_receiver.py`**：摄像头图像流 UDP 接收 + JPEG 解码 + OpenCV 显示

## 通信

### 传感器数据 (ESP32 → PC)

| 参数 | 值 |
|------|-----|
| ESP32-P4 IP | DHCP 自动获取 (当前 10.16.234.86) |
| 上位机 IP | 10.16.234.215 |
| 端口 | 8080 UDP |
| 间隔 | 每 2 秒 |
| 格式 | JSON (10 字段: ts, dht11_t/h, ds18b20_t, mq135_v, light_v, alert, err, reason, fan, led) |

```bash
# 启动上位机接收端
D:\Anaconda3\envs\ForAgents\python.exe pc_receiver.py
```

### 摄像头图像流 (ESP32-CAM → ESP32-P4 → PC)

| 参数 | 值 |
|------|-----|
| 摄像头模块 | ESP32-CAM (Arduino CameraWebServer) |
| 采集方式 | ESP32-P4 通过 HTTP GET `/capture` 拉取 JPEG |
| 转发协议 | UDP 分包 (Magic 0xAA55, 4096 字节/包) |
| 端口 | 8082 UDP |
| 帧率 | 3 fps (可配置 1-10) |
| 配置项 | `CONFIG_CAMERA_HTTP_FPS` / `CONFIG_CAMERA_HTTP_ESP32CAM_URL` |

```bash
# 启动接收端 (先开)
D:\Anaconda3\envs\ForAgents\python.exe f:/CodeProject/iiot_Experiment_2/code/SmartMonitor/camera_display_receiver.py
```

> ESP32-CAM 端需先烧录 Arduino CameraWebServer 示例，P4 侧通过 Kconfig 配置其 IP。
>
> **备选方案**：也可用本地 USB 摄像头 (KYT-U400) + `camera_capture_sender.py` 直连 PC，详见 `CAMERA_DEBUG_LOG.md`。

## 关键约束

- PSRAM 已启用 32MB（200MHz 16线模式），支持 ESP-IDF 堆分配器自动使用外部内存
- MQ-135 上电预热 ≥ 3 分钟数据稳定
- DS18B20 12 位精度 0.0625°C，转换时间 ≥ 750ms
- OLED I2C 需 4.7KΩ 上拉电阻，已启用内部上拉
- ESP-Hosted SDIO Wi-Fi 初始化需约 13s，任务启动后通过 `esp_netif_is_netif_up()` 轮询等待（最多 20s）
- Camera HTTP 拉图与 Sensor UDP 共用 lwIP pbuf 池，已实现 ENOMEM(errno=12) 退避重试 + 每包 5ms 微延迟防止资源争抢
- Camera UDP 端口 8082 与 Sensor 数据端口 8080 隔离
