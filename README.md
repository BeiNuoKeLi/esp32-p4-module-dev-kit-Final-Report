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

### 整体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      ESP32-P4 边缘监测终端                              │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  DHT11  DS18B20  MQ-135  光敏  ──→  FreeRTOS 多任务               │  │
│  │                                               ↓                   │  │
│  │                                   ┌───────────────────────┐       │  │
│  │                                   │   OLED 本地显示        │       │  │
│  │                                   │   声光报警 (Buzzer/LED)│       │  │
│  │                                   │   继电器控制           │       │  │
│  │                                   └───────────┬───────────┘       │  │
│  │                                               ↓                   │  │
│  │              UDP:8080 ──→ Docker 内置监听器 (SensorUDPProtocol)    │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                      Docker Web 仪表盘 (v3.5)                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐    │
│  │   FastAPI       │  │   AIOSQLite     │  │   WebSocket         │    │
│  │   REST API      │  │   数据持久化    │  │   实时推送          │    │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬──────────┘    │
│           │                   │                      │                 │
│           ↓                   ↓                      ↓                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │               Chart.js 前端仪表盘                               │   │
│  │   传感器卡片 + 报警状态 + 历史曲线 + 摄像头流 + 仓储管理         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 摄像头数据流

```
ESP32-CAM ──UDP 分片推流──► VPS :8003  ← ★ 服务器部署 (CAMERA_MODE=udp_esp32, v4.0 推荐)
  │   CameraWebServer.ino                                   ↓
  │   WiFiUDP 直连, 协议 [0xAA55+FrameID+ChunkIdx+TotalChunks][JPEG分片]  Docker camera_server
  │   帧驱动 ~10fps, 零连接开销, 无 RTT 窗口限制               ↓
  │   跨海 UDP 吞吐 238Mbps vs TCP 2Mbps (119x)        ReadableStream + BlobURL 逐帧渲染
  │   UDP 特性: 无条件全速推流, 不支持按需降速 (TCP 模式下有)

ESP32-CAM ──TCP 二进制推流──► VPS :8003  ← 备选 (CAMERA_MODE=tcp, v3.8)
  │   WiFiClient 长连接, 帧格式 [2B len][JPEG]             ↓
  │   每 3s 心跳 GET /api/camera/push_status, 无观看者降速 1fps

ESP32-CAM ──HTTP POST──► VPS :8001 /api/camera/push  ← 备选 (CAMERA_MODE=push)
  │   (每帧 HTTP 握手, ~1-3fps)

ESP32-CAM ──HTTP/TCP──► Docker (camera_server.py)  ← 局域网模式 (CAMERA_MODE=http)
  │   CameraWebServer.ino 提供 /capture + /stream      ↓
  └──────────────────────────────            Web 仪表盘实时显示
```

### 模块说明

- **`sensors.c/h`**：DHT11 / DS18B20 / MQ-135 / 光敏 / 蜂鸣器 / LED / 继电器 驱动
- **`oled_ssd1306.c/h`**：SSD1306 I2C 驱动，Page Addressing 逐页刷新
- **`udp_sender.c/h`**：JSON 组包 + UDP Socket 发送 (snprintf, lwip/sockets.h)，带 ENOMEM 退避重试
- **`smart_monitor_main.c`**：主入口，Wi-Fi STA + 8 个 FreeRTOS 任务创建（LED/继电器集成在 buzzer 任务中）
- **`pc_receiver.py`**：Windows 上位机 UDP 接收脚本 (监听 8080, CSV 日志)
- **`camera_display_receiver.py`**：~~摄像头图像流 UDP 接收 + JPEG 解码 + OpenCV 显示~~ **已弃用**，由 Docker `camera_server.py` 替代
- **`camera_capture_sender.py`**：~~USB 摄像头采集 + UDP 发送~~ **已弃用**，由 `CameraWebServer/` (ESP32-CAM) 替代
- **`smart_monitor_sim_gui.py`**：综合工具体 v3.0 — 仿真控制 (Tab 1) + 仓储管理 (Tab 2)，摄像头预览以独立 Toplevel 窗口显示
- **`warehouse_db.py`**：SQLite 数据库模块 (inventory 库存表 + check_log 操作日志)
- **`generate_qr_labels.py`**：二维码标签批量生成工具 (6 种与传感器匹配的化肥)
- **`CameraWebServer/`**：ESP32-CAM Arduino 相机服务端源码（OV2640 QVGA JPEG 采集）

## 通信

### Web 仪表盘 (v3.5)

项目已支持 **Docker 容器化部署**，提供 Web 可视化仪表盘：

```bash
# 启动方式
cd docker
docker-compose up -d

# 拉取最新代码 + 重建容器（服务器部署一键更新）
cd /opt/esp32-p4-module-dev-kit-Final-Report && git checkout main && git pull && docker-compose -f docker/docker-compose.yml up -d --build
```

#### 本地开发

| 服务 | 地址 | 说明 |
|------|------|------|
| Web 仪表盘 | http://localhost:8000 | Chart.js 实时数据图表 |
| API 文档 | http://localhost:8000/docs | FastAPI 交互式文档 |
| WebSocket | ws://localhost:8000/ws | 实时数据推送（传感器 + 仓储变更 + 报警事件即时同步） |

#### VPS 部署 (公网端口映射)

| 服务 | 容器内 | 对外 | 说明 |
|------|--------|------|------|
| Web + CAM 推帧 + 心跳 | :8000 | `:8001` (TCP) | FastAPI HTTP + `/api/camera/push_status` |
| P4 传感器 UDP | :8080 | `:8002` (UDP) | SensorUDPProtocol |
| CAM UDP 推流 | :8003 | `:8003` (UDP) | ★ ESP32-CAM UDP 分片推流 (v4.0, 跨海119x吞吐, 无RTT影响) |

**Web 仪表盘功能**：
- 实时传感器数据卡片（温湿度、MQ-135、光敏）
- 分级报警状态徽章（L0~L3），L3 紧急时页面红色闪烁
- **报警历史系统**：事件列表/详情/统计/确认，去重窗口 30s，级别变化即时触发
- 历史数据折线图（温度/湿度趋势）
- **WebSocket 实时同步**：库存变更、报警确认等操作即时推送到所有在线客户端，无需手动刷新
- 摄像头 MJPEG 实时流预览 + **视频流开关**（关闭即切黑屏节省带宽，开启恢复拉流；TCP 模式下无观看者时 ESP32-CAM 自动切心跳模式省带宽 ~99.8%，UDP 模式全速推流不支持按需降速）
- 仓储管理（二维码扫码入库/出库 + 手动新增 + 库存分类统计 + 流水清空 + 一键清除库存）
- **仿真注入面板**：前端一键注入 L1/L2/L3 预设报警或自定义传感器数值
- **报警管理**：一键清空全部报警记录（DELETE /api/alarms）
- **内置 UDP 监听器**：Docker 服务直接监听 :8080，无需外部 udp_to_web.py 桥接脚本
- SQLite 数据持久化（aiosqlite 异步引擎）
- **演示锁定机制**：Cookie 独立锁，每浏览器需密码解锁后才能执行写操作，只读数据不受限

#### 演示锁定配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `DEMO_PASSWORD` | `111` (docker-compose 部署时启用) | 演示密码，留空则不启用锁定功能。`docker-compose.yml` 中已预置 `111` |

**工作原理**：
1. 设置 `DEMO_PASSWORD` 后，Docker 启动时仪表盘自动进入只读模式
2. 所有 POST/PUT/DELETE 写操作需浏览器持有 `demo_unlock` Cookie（HMAC-SHA256 签名，防伪造）
3. 点击 Header 的 🛡️ 锁定指示器 → 弹出密码框 → 输入密码解锁
4. 每个浏览器独立锁定：你的浏览器解锁后，别人的浏览器仍然锁定
5. 关闭浏览器即自动失效（会话级 Cookie），点击 🛡️ 可主动重新锁定
6. ESP32 设备通信端点（`/api/sensors`、`/api/camera/push` 等）白名单放行，不受锁定影响

### 传感器数据 (ESP32 → PC/VPS)

| 参数 | 本地开发 | VPS 部署 |
|------|----------|----------|
| ESP32-P4 IP | DHCP 自动获取 | 手机热点 DHCP |
| 目标 IP | 10.16.234.215 | 38.55.199.220 |
| 目标端口 | 8080 UDP | 8002 UDP |
| 间隔 | 每 2 秒 | 每 2 秒 |
| 格式 | JSON (12 字段) | JSON (12 字段) |

```bash
# 启动上位机接收端
D:\Anaconda3\envs\ForAgents\python.exe pc_receiver.py
```

### 摄像头图像流

#### UDP 分片推流：ESP32-CAM 直推 VPS (服务器部署，v4.0 推荐)

| 参数 | 值 |
|------|-----|
| 采集方式 | ESP32-CAM `loop()` 帧驱动 + WiFiUDP 分片推流 |
| 目标 | `38.55.199.220:8003` (UDP raw) |
| 协议格式 | `[0xAA55 Magic + FrameID(u16) + ChunkIdx(u16) + TotalChunks(u16)][JPEG分片]` |
| 分片大小 | 每包 ≤1408 字节（安全互联网 MTU，适配各种 NAT/路由） |
| 环境变量 | `CAMERA_MODE=udp_esp32` |
| 帧率 | ~10 fps（帧驱动，不受 RTT 影响） |
| 前端显示 | `fetch` → ReadableStream → boundary 二进制切分 → BlobURL |
| 按需推送 | **不支持**（UDP 无连接态，ESP32-CAM 无条件全速推流） |
| 容错机制 | VPS 侧分片重组 + 超时 5s + 提前渲染 1s（≥50% 分片即渲染） |

#### TCP 二进制推流 (备选，v3.8)

| 参数 | 值 |
|------|-----|
| 环境变量 | `CAMERA_MODE=tcp` |
| 帧格式 | `[2-byte big-endian length][JPEG data]` (无 HTTP 开销) |
| 帧率 | ~10-16 fps（局域网）/ ~6-10 fps（跨海，受 RTT ~154ms TCP窗口限制） |
| 心跳端点 | `GET /api/camera/push_status`（无观看者时降速 1fps） |
| 省带宽 | 无观看者时降为 1fps，节省 ~95% 带宽 |

#### 主模式：Docker 直连 ESP32-CAM (局域网推荐)

| 参数 | 值 |
|------|-----|
| 摄像头模块 | ESP32-CAM (OV2640, **HVGA 480×320**, quality=12, contrast=2 锐化边缘 + aec2 快速曝光 → QR 扫码优化) |
| CAM 源码 | `CameraWebServer/` (Arduino 工程，提供 `/capture` + `/stream`) |
| 采集方式 | Docker `camera_server.py` 直接 HTTP GET `/capture` 拉取 JPEG (TCP 零丢包) |
| 环境变量 | `CAMERA_MODE=http`, `ESP32_CAM_URL`, `CAMERA_HTTP_FPS` |
| 默认帧率 | 5 fps |
| 前端显示 | `fetch` → ReadableStream → boundary 二进制切分 → BlobURL |

### 仓储管理 (二维码扫码)

```bash
# 1. 生成二维码标签
D:\Anaconda3\envs\ForAgents\python.exe generate_qr_labels.py

# 2. 启动综合工具体
D:\Anaconda3\envs\ForAgents\python.exe smart_monitor_sim_gui.py
```

| 文件 | 说明 |
|------|------|
| `warehouse_db.py` | SQLite 数据库 (inventory + check_log 表) |
| `generate_qr_labels.py` | 生成 6 种化肥二维码标签到 `qr_labels/`（version=4, EC=H 30% 纠错, 抗污损能力翻倍） |
| `smart_monitor_sim_gui.py` Tab 2 | 摄像头拉流 → CLAHE + 锐化核预处理 → pyzbar 扫码（0.5s 冷却） → 入库/出库 → TreeView 表格 + 一键清除库存按钮
| 摄像头预览 | 点击顶部「打开摄像头预览」按钮，独立窗口 640×480+ 展示画面

## 已知问题 & 修复

| 问题 | 原因 | 修复 |
|------|------|------|
| DHT11 高电平超时 (~95% 失败率) | ESP32-P4 双核中断 + `esp_rom_delay_us(10)` 粒度过粗 | 改为 1µs 粒度脉冲宽度直接测量，45µs 阈值区分 bit0/bit1 |

## 关键约束

- PSRAM 已启用 32MB（200MHz 16线模式），支持 ESP-IDF 堆分配器自动使用外部内存
- MQ-135 上电预热 ≥ 3 分钟数据稳定
- DS18B20 12 位精度 0.0625°C，转换时间 ≥ 750ms
- OLED I2C 需 4.7KΩ 上拉电阻，已启用内部上拉
- ESP-Hosted SDIO Wi-Fi 初始化需约 13s，任务启动后通过 `esp_netif_is_netif_up()` 轮询等待（最多 20s）
- Camera HTTP 拉图与 Sensor UDP 共用 lwIP pbuf 池，已实现 ENOMEM(errno=12) 退避重试 + 1 tick 微延迟防止资源争抢
- Camera UDP 端口 8082 与 Sensor 数据端口 8080 隔离
- Docker 直连模式 (CAMERA_MODE=http) 绕过 P4 UDP 中继，TCP 协议保证帧完整性，无分片/丢包问题
- VPS 部署推荐 (CAMERA_MODE=udp_esp32)：ESP32-CAM WiFiUDP 分片直推 VPS:8003，跨海吞吐 238Mbps，零 RTT 影响。UDP 模式无条件全速推流，不支持按需降速/心跳
- VPS 部署备选 (CAMERA_MODE=tcp)：ESP32-CAM TCP 长连接推流，支持心跳降速省带宽，但受 RTT 窗口限制（跨海 ~10fps）
- VPS 部署旧方案 (CAMERA_MODE=push)：ESP32-CAM 主动 HTTP POST 推帧到 `/api/camera/push`，已基本弃用
