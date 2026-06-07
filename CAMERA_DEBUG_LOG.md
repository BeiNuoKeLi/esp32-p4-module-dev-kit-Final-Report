# 摄像头调试记忆 & 当前状态

> 最后更新: 2026-06-07 19:50
> 硬件: KYT-U400 工业 USB UVC 摄像头 / ESP32-CAM (OV2640)
> 操作系统: Windows 11, Python 3.x (D:\Anaconda3\envs\ForAgents)

---

## 架构概览

```
┌──────────────────────────────────────────────────────┐
│  camera_capture_sender.py   (发送端, 本文件)          │
│  ① OpenCV DirectShow 打开摄像头                       │
│  ② 捕获原始帧 (640×360, MJPG)                         │
│  ③ Gamma 校正 + 锐化 (软件补偿)                        │
│  ④ JPEG 编码 (quality=80)                             │
│  ⑤ UDP 分包 (4096 字节/包) → 127.0.0.1:8082          │
└──────────────────────────┬───────────────────────────┘
                           │ localhost UDP
┌──────────────────────────▼───────────────────────────┐
│  camera_display_receiver.py (接收端)                  │
│  ① 监听 UDP 0.0.0.0:8082                             │
│  ② 按 FrameID + ChunkIdx 重组分片                      │
│  ③ OpenCV imshow 实时显示 + FPS 叠加                   │
└──────────────────────────────────────────────────────┘

协议: camera_protocol.py (Magic 0xAA55, 8 字节头, 4096 字节 payload)
```

---

## 当前配置 (最终稳定版)

### 发送端 (`camera_capture_sender.py`)

| 参数 | 值 | 说明 |
|------|-----|------|
| `DEFAULT_CAMERA_ID` | `1` | USB 摄像头设备 ID |
| `DEFAULT_TARGET_IP` | `127.0.0.1` | 本机回环 (ESP32 上线改 `10.16.234.215`) |
| `DEFAULT_TARGET_PORT` | `8082` | 图片 UDP 端口 |
| `DEFAULT_FPS` | `10` | 目标帧率 |
| `FRAME_WIDTH` | `640` | 捕获宽度 |
| `FRAME_HEIGHT` | `360` | 捕获高度 |
| `JPEG_QUALITY` | `80` | JPEG 质量 (0-100) |
| `GAMMA` | `0.55` | Gamma 校正 (<1 提亮暗部) |
| `SHARPEN_STRENGTH` | `0.2` | 锐化强度 |
| VideoCapture 后端 | `cv2.CAP_DSHOW` | DirectShow (兼容工业相机) |
| FOURCC | `MJPG` | 高帧率相机原生格式 |

### 接收端 (`camera_display_receiver.py`)

| 参数 | 值 | 说明 |
|------|-----|------|
| `DEFAULT_LISTEN_PORT` | `8082` | 监听端口 |
| `BUFFER_SIZE` | `65536` | UDP 接收缓冲区 |
| `SOCK_TIMEOUT` | `0.002` | Socket 超时 (2ms 高频轮询) |
| `FRAME_TIMEOUT` | `0.3` | 帧超时 (秒) |
| `SO_RCVBUF` | `1024*1024` | OS 接收缓冲 1MB |
| 最多缓存帧数 | `3` | 防止延迟累积 |

### 协议 (`camera_protocol.py`)

| 参数 | 值 | 说明 |
|------|-----|------|
| `MAGIC` | `0xAA55` | 帧同步标识 |
| `HEADER_SIZE` | `8` | 协议头字节数 |
| `MAX_PAYLOAD` | `4096` | 单包有效载荷 |

---

## 调试历史

### 阶段 1: 初始创建
- 创建三个文件：`camera_capture_sender.py`、`camera_display_receiver.py`、`camera_protocol.py`
- 初始分辨率: 640×480, FPS=3, JPEG_QUALITY=70

### 阶段 2: 分辨率调整
- 640×480 → **640×360** (用户要求)

### 阶段 3: MSMF 后端捕获失败
- **问题**: `cap_msmf.cpp OnReadSample() is called with error status: -2147024890`
- **根因**: MSMF 后端不兼容 KYT-U400 工业相机格式
- **修复**: 改用 `cv2.CAP_DSHOW` (DirectShow) 后端 + `FOURCC=MJPG`

### 阶段 4: 画面太暗 (第一轮)
- **问题**: 工业摄像头默认曝光/增益偏低，画面极暗
- **尝试**: `EXPOSURE=-6`, `GAIN=80`, `BRIGHTNESS=128`, `CONTRAST=140`
- **结果**: 略有改善但仍暗

### 阶段 5: 帧率提升 & 画面卡顿
- 帧率 3fps → **15fps**
- **问题**: 帧非线性跳动，画面卡顿，延迟几十秒
- **根因**: `next_frame_time` 漂移不重置 → sleep 被跳过 → 全速发帧 → UDP 队列爆炸
- **修复**:
  - 帧率控制改用 `last_send` 基准计时
  - 帧率降为 **10fps**
  - `MAX_PAYLOAD` 1016 → **4096** (减少分片数: 11包→3包)
  - `JPEG_QUALITY` 70 → **50**
  - `SOCK_TIMEOUT` 5ms → **2ms**
  - 帧缓存 8 → **3**

### 阶段 6: 延迟随时间递增
- **问题**: 延迟随时间线性增长
- **根因**: imshow 阻塞收包，每帧亏 ~15ms，持续积累
- **修复**: 进一步降低缓存帧数、缩短帧超时

### 阶段 7: 画面太暗 (第二轮, 软件补偿)
- **问题**: 画面仍然极暗，几乎什么都看不见
- **修复**:
  - 硬件: `AUTO_EXPOSURE=0.75`, `EXPOSURE=-9`, `GAIN=255`
  - 软件: **Gamma 校正** (γ=0.45, LUT 查表法) + **锐化** (filter2D unsharp kernel)

### 阶段 8: 画面太糊 (核心问题)
- **问题**: 画面模糊，像没对上焦
- **发现**: 用户在 VideoCapture 独立控制面板中已调好最清晰 (焦点=5, 曝光=-5, 自动对焦开启)
- **根因识别**:
  1. **JPEG_QUALITY=50** 过度压缩 → 块效应 → 画面"糊"
  2. **GAMMA=0.45** 过度拉伸暗部 → 放大压缩伪影和噪点
  3. **SHARPEN_STRENGTH=0.6** 强锐化 → 硬件已清晰时反而破坏画质
  4. **OpenCV `cap.set`** 覆盖了 VideoCapture 已调好的焦点/曝光/增益参数
- **修复**:
  1. **删除所有 `cap.set` 图像参数** (亮度/对比度/曝光/增益/对焦)，让 VideoCapture 的硬件设定生效
  2. `JPEG_QUALITY` 50 → **80**
  3. `GAMMA` 0.45 → **0.55**
  4. `SHARPEN_STRENGTH` 0.6 → **0.2**

---

## 关键经验教训

1. **不要用 OpenCV `cap.set()` 覆盖 UVC 扩展控制参数**
   - OpenCV 的 DirectShow 后端对 UVC 扩展控制 (对焦/曝光/增益) 兼容性差
   - 在专用软件 (如 VideoCapture/AmCap) 中调好参数后，OpenCV 只应读取，不应写入
   - 目前唯一保留的 `cap.set` 参数: `FOURCC= MJPG`, `FPS`, `BUFFERSIZE`, 分辨率

2. **暗环境问题靠硬件 + 轻软件双重解决**
   - VideoCapture 中调自动曝光 + 增益 (硬件层)
   - Python 中 gamma=0.55 轻提亮 (软件层, 不过度)

3. **JPEG 质量直接影响清晰度**
   - quality=50 在低分辨率下块效应显著
   - quality=80 是画质与包大小的平衡点

4. **帧率控制必须使用 last_send 基准计时**
   - `next_frame_time += interval` 模式会漂移
   - `time.sleep(interval - elapsed)` 模式保证绝不超速

---

## 运行命令

### 启动接收端 (先开)
```powershell
D:\Anaconda3\envs\ForAgents\python.exe f:/CodeProject/iiot_Experiment_2/code/SmartMonitor/camera_display_receiver.py
```

### 启动发送端 (后开)
```powershell
D:\Anaconda3\envs\ForAgents\python.exe f:/CodeProject/iiot_Experiment_2/code/SmartMonitor/camera_capture_sender.py --camera 1
```

### 可选参数
```powershell
# 自定义帧率/分辨率/目标地址
--camera 0         # 摄像头 ID
--fps 10           # 帧率
--ip 127.0.0.1     # 目标 IP
--port 8082        # 目标端口
```

---

## 待调参数速查

| 调节目标 | 修改文件 | 行 | 变量 | 方向 |
|---------|---------|----|------|------|
| 画面仍太暗 | `camera_capture_sender.py` | 46 | `GAMMA` | ↓ (如 0.45) |
| 画面仍太亮 | `camera_capture_sender.py` | 46 | `GAMMA` | ↑ (如 0.70) |
| 画面还是糊 | `camera_capture_sender.py` | 45 | `JPEG_QUALITY` | ↑ (如 90) |
| 噪点太多 | `camera_capture_sender.py` | 45/47 | 提高 `JPEG_QUALITY` 或 降 `SHARPEN_STRENGTH` | - |
| 帧率提高 | 命令行 `--fps` | - | - | 15 或 20 |
| 分辨率调整 | `camera_capture_sender.py` | 43-44 | `FRAME_WIDTH/HEIGHT` | - |

---

## ESP32-P4 中继模式 (2026-06-07)

当 PC USB 摄像头不可用或需要远程监控时，ESP32-P4 可通过 HTTP 从局域网内的 **ESP32-CAM** 拉取 JPEG 并 UDP 转发到 PC：

```
ESP32-CAM (OV2640) ──HTTP GET /capture──→ ESP32-P4 ──UDP :8082──→ PC (camera_display_receiver.py)
```

### 配置方式

通过 `idf.py menuconfig` → `Example Configuration` 设置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CAMERA_HTTP_ENABLED` | y | 启用/禁用中继任务 |
| `CAMERA_HTTP_ESP32CAM_URL` | `http://10.16.234.23/capture` | ESP32-CAM 地址 |
| `CAMERA_HTTP_UDP_IP` | `10.16.234.215` | PC 端 IP |
| `CAMERA_HTTP_FPS` | 3 | 拉图帧率 (1-10) |

### 实现文件

- `main/camera_http_fetch.c/h` — FreeRTOS 任务: HTTP GET → JPEG 缓冲 → 0xAA55 分包 → sendto
- 协议与 `camera_protocol.py` 完全兼容, PC 端用同一个 `camera_display_receiver.py` 即可

### 已验证结果

- ESP32-CAM @ 10.16.234.23, JPEG ~5-7KB, 3fps 稳定
- HTTP 延迟 200-600ms, 每帧 2 包 UDP
- 与 Sensor JSON UDP 并发无冲突 (ENOMEM 退避 + 5ms 微延迟)
