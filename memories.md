# ESP32-P4 智能环境监测系统 - 项目备忘录

> **文档目的**：记录项目当前状态、规范和已实现功能，方便后续 Agent 理解和继续开发
>
> **最后更新**：2026-06-16（v3.5 演示锁定机制 + 手机端响应式适配；900/600/480 三级断点）

---

## 一、项目概述

### 1.1 基本信息

| 项目 | 内容 |
|------|------|
| **项目名称** | ESP32-P4 智能环境监测系统 |
| **开发板** | 微雪(Waveshare) ESP32-P4-Module-DEV-KIT |
| **芯片** | ESP32-P4 (eco2, silicon v1.0, RISC-V 双核) |
| **框架** | ESP-IDF v5.5.1 |
| **编译器** | RISC-V 32-bit |
| **通信** | WiFi STA (通过 SDIO → ESP32-C6 协处理器) |
| **上位机** | Windows, IP: 10.16.234.215:8080 (UDP) (2026-06-04 ipconfig 确认) |

### 1.2 项目目标

实现一个多传感器环境监测系统，采集温湿度、空气质量、光照强度等数据，通过 WiFi UDP 协议发送到上位机进行显示和记录。

---

## 二、已实现功能 ✅

### 2.1 传感器驱动

| 传感器 | 引脚 | 状态 | 说明 |
|--------|------|------|------|
| **DS18B20** | GPIO1 | ✅ 已验证 | 高精度温度传感器，0.0625°C分辨率 |
| **MQ-135** | GPIO21(AO) / GPIO22(DO) | ✅ 已验证 | 空气质量传感器，ADC1_CH5，带预热检测 |
| **DHT11** | GPIO2 | ✅ 已验证 | 温湿度传感器，精度 ±2°C / ±5%RH |
| **光敏电阻** | GPIO20(AO) / GPIO23(DO) | ✅ 已验证 | 光照强度测量，使用 ADC1_CH4 |
| **OLED SSD1306** | GPIO7(SDA) / GPIO8(SCL) | ✅ 已验证 | 0.96寸 I2C OLED，8行 6x8 字体，1秒刷新 |
| **蜂鸣器** | GPIO25 | ✅ 已验证 | 有源蜂鸣器，S8050 NPN驱动，间歇鸣叫 |

### 2.2 传感器参数

#### DS18B20 温度传感器
- **温度范围**：-55°C ~ +125°C
- **温度精度**：±0.5°C (-10°C ~ 85°C)
- **分辨率**：12位，**0.0625°C**
- **转换时间**：12位分辨率最大 750ms
- **通信协议**：1-Wire，64位 ROM ID
- **ROM 命令**：0xCC (跳过ROM), 0x44 (开始转换), 0xBE (读暂存器)
- **温度计算**：`Temp = (H << 8 | L) / 16.0`

#### MQ-135 空气质量传感器
- **模块供电**：5V（传感器加热）或 3.3V（灵敏度略低）
- **检测气体**：氨气、硫化物、烟雾
- **检测浓度**：10 ~ 1000 ppm
- **预热时间**：上电后 ≥ 3 分钟读数稳定（技术手册）
- **AO 特性**：浓度越高 → 电压越高（模块基础参数）
- **DO 特性**：TTL 低电平有效（超阈值→0，信号灯亮→1）
- **AO 采集**：ADC1_CH5/GPIO21, 12位精度, 算术平均滤波
- **电压换算**：`voltage = ao_raw × 3.3 / 4095`（REQUIREMENT.md 5.4）
- **DO 电平转换**：模块 5V 供电时 DO=5V TTL，需 2KΩ:1KΩ 分压至 3.3V（3.3V供电则无需转换）

#### MQ-135 预热机制
- **问题**：上电瞬间 DO 输出不稳定，导致误报警
- **解决方案**：预热到 DO 稳定为 1（连续 5 次读数为 1）或最大 60 秒超时
- **实现**：`s_mq135_warmed_up` 静态变量 + `mq135_is_warmed_up()` 查询函数
- **显示**：预热期间 OLED 显示 "MQ135: Warming..."
- **注意**：预热状态使用**静态变量**，不访问 `g_sensor_data` 避免与 ESP-Hosted SDIO 中断冲突

#### DHT11 温湿度传感器
- **温度范围**：0 ~ 50°C
- **温度精度**：±2°C，分辨率 1°C (整数)
- **湿度范围**：20% ~ 90%RH
- **湿度精度**：±5%RH (25°C时为±4%RH)
- **采样周期**：≥ 2 秒
- **通信协议**：1-Wire，40位数据 (5字节)
- **校验方式**：DATA[4] == DATA[0]+DATA[1]+DATA[2]+DATA[3]

#### 光敏电阻传感器
- **工作电压**：3.3V ~ 5V
- **AO 特性**：光照越强 → 电压越高
- **DO 特性**：低于阈值→高电平(1)，超过阈值→低电平(0)
- **ADC**：12位精度 (0~4095)，使用算术平均滤波

#### 蜂鸣器
- **控制引脚**：GPIO25（推挽输出）
- **驱动电路**：GPIO25 → 1KΩ → S8050 基极；集电极 → 有源蜂鸣器 → 3.3V
- **报警逻辑**：
  - MQ-135 DO=0 或 光敏 DO=0 → 间歇鸣叫（100ms 鸣 / 500ms 停）
  - **MQ-135 预热期间 DO 不参与报警判断**

### 2.3 软件架构

```
main/
├── smart_monitor_main.c    # 主入口，8任务(WiFi STA + OLED + Buzzer + UDP Send + UDP Sim + Cam HTTP)
├── sensors.h             # 传感器+蜂鸣器驱动头文件 + sensor_shared_t
├── sensors.c            # 传感器驱动实现 (MQ-135预热 + DS18B20 + DHT11 + 光敏 + 蜂鸣器)
├── oled_ssd1306.h        # SSD1306 OLED 驱动头文件 (I2C, GPIO7/8, 6x8字体)
├── oled_ssd1306.c        # SSD1306 OLED 驱动实现 (128x64 framebuffer, Page Addressing 逐页刷新)
├── udp_sender.h         # ✅ UDP 任务声明（2026-06-04 实现, 2026-06-07 ENOMEM 重试）
├── udp_sender.c         # ✅ JSON 组包 + UDP Socket 发送（ENOMEM 退避 + Wi-Fi 轮询等待）
├── camera_http_fetch.h  # ✅ Camera HTTP Relay 声明（2026-06-07 新增）
└── camera_http_fetch.c  # ✅ HTTP 拉取 ESP32-CAM JPEG → UDP 分包转发（2026-06-07 新增）
└── pc_receiver.py       # ✅ Windows 上位机 UDP 接收脚本（2026-06-04 实现）

# 摄像头图像流模块 (2026-06-06 实现)
camera_capture_sender.py    # ✅ 摄像头采集 + Gamma校正/锐化 + JPEG编码 + UDP分包发送
camera_display_receiver.py  # ✅ UDP接收 + 分片重组 + JPEG解码 + OpenCV实时显示
camera_protocol.py          # ✅ Magic 0xAA55 协议头编解码 (大端序, 8字节头, 4096字节payload)
CAMERA_DEBUG_LOG.md         # 摄像头调试历史 & 参数速查

# Docker Web 仪表盘 v3.5 (2026-06-16 更新)
docker/
├── docker-compose.yml      # ✅ 容器编排 (UDP 8080 端口暴露 + ESP32_IP + DEMO_PASSWORD 环境变量)
├── app/
│   ├── main.py            # ✅ FastAPI + WebSocket + 内置 UDP 监听器 + 仿真注入 API + 演示锁定中间件
│   ├── database.py        # ✅ AIOSQLite (清空报警/清空流水/新增物料/删除物料)
│   ├── models.py          # ✅ Pydantic 模型 (SimInjectRequest, SimStatus)
│   └── static/
│       └── dashboard.html # ✅ Chart.js 仪表盘 (仿真面板 + 库存统计 + 报警清空 + 🛡️锁定指示器)
└── ...
```

### 2.4 OLED 显示内容（8行布局）

| 行 | 内容 | 说明 |
|----|------|------|
| 0 | `Smart Monitor` | 系统标题 |
| 1 | `DHT11:T=29C H=54%` | 空气温湿度（整数精度） |
| 2 | `DS18B20: 27.875 C` | 高精度温度（0.0625°C分辨率） |
| 3 | `MQ135: Warming...` | 预热中（预热完成后显示电压和状态，如 `MQ135: 0.99V OK`） |
| 4 | `Light: 1375 OK` | 光照强度（ADC原始值 + 报警状态） |
| 5 | `Alrt:OFF Err:0x00` | 报警汇总 + 传感器错误码（预热期间 MQ-135 不计入） |
| 6 | `Buzzer: OFF` | 蜂鸣器当前状态 |
| 7 | 空行 | 预留扩展 |

---

## 三、硬件接线规范

### 3.1 引脚分配表（已验证）

| 传感器 | 信号 | ESP32-P4 GPIO | 类型 |
|--------|------|---------------|------|
| DS18B20 | DATA | **GPIO 1** | 数字 I/O（开漏，需上拉 4.7KΩ） |
| DHT11 | DATA | **GPIO 2** | 数字 I/O（开漏，需上拉 5KΩ） |
| MQ-135 | AO | **GPIO 21** | ADC1_CH5（模拟输入） |
| MQ-135 | DO | **GPIO 22** | 数字输入（5V供电需电平转换 5V→3.3V） |
| 光敏电阻 | AO | **GPIO 20** | ADC1_CH4（模拟输入） |
| 光敏电阻 | DO | **GPIO 23** | 数字输入 |
| OLED SSD1306 | SDA | **GPIO 7** | I2C 数据线 |
| OLED SSD1306 | SCL | **GPIO 8** | I2C 时钟线 |
| 蜂鸣器 | 控制 | **GPIO 25** | 数字输出（S8050 NPN驱动） |

### 3.2 电路要求

| 项目 | 要求 |
|------|------|
| DHT11 DATA 上拉 | 外接 **5KΩ** 电阻至 3.3V（线长 < 20m） |
| DS18B20 DATA 上拉 | 外接 **4.7KΩ** 电阻至 3.3V |
| MQ-135 DO 电平转换 | 模块 5V 供电时 DO = 5V TTL，必须经 2KΩ:1KΩ 电阻分压降至 3.3V（3.3V供电无需转换） |
| MQ-135 预热 | 上电后预热到 DO 稳定为 1（约数秒~数十秒），最长 60 秒超时 |
| DHT11 上电稳定 | 上电后 **等待 ≥ 1 秒** 越过不稳定状态 |
| OLED I2C 上拉 | SDA(IO7)/SCL(IO8) 各接 **4.7KΩ** 电阻至 3.3V（或开启内部上拉） |
| 蜂鸣器驱动 | GPIO25 → 1KΩ → S8050基极, 集电极→蜂鸣器→3.3V, 发射极→GND |

---

## 四、软件规范

### 4.1 数据结构

```c
/* 传感器共享数据结构体 (sensors.h, OLED 显示用) */
typedef struct {
    /* DHT11 */
    int     dht11_temp, dht11_humi, dht11_err;

    /* DS18B20 */
    float   ds18b20_temp;
    int     ds18b20_err;

    /* MQ-135 */
    int     mq135_ao_raw;      /* AO 原始值 */
    float   mq135_voltage;      /* 电压 (V) */
    int     mq135_do;           /* DO: 0=超阈值, 1=正常 */
    int     mq135_err;         /* 错误标志 */

    /* 光敏电阻 */
    int     photo_raw;          /* AO 原始值 */
    int     photo_do;           /* DO: 0=超阈值, 1=正常 */
    int     photo_err;          /* 错误标志 */

    /* 蜂鸣器 */
    int     buzzer_on;          /* 蜂鸣器状态: 0=静音, 1=鸣叫 */
} sensor_shared_t;

/* 全局共享数据句柄 */
extern sensor_shared_t g_sensor_data;
extern SemaphoreHandle_t g_sensor_mutex;
```

### 4.2 错误处理规范

| 错误 | 检测方式 | 错误码 |
|------|----------|--------|
| DHT11 校验失败 | checksum != DATA[0]+DATA[1]+DATA[2]+DATA[3] | err \|= 0x01 |
| DHT11 无响应 | 复位后检测响应失败 | err \|= 0x01 |
| 光敏 ADC 异常 | 12 次采样全为 0 或全为 4095 | err \|= 0x08 |
| DS18B20 无响应 | One_Wire_Init() 返回非 0 | err \|= 0x02 |
| MQ-135 ADC 异常 | 12 次采样全为 0 或全为 4095 | err \|= 0x04 |

### 4.3 ADC 配置规范

> ⚠️ **API 选择**：使用 ESP-IDF v5.x 新版 `esp_adc/adc_oneshot.h`，旧版 API 已废弃

```c
/* 初始化 ADC1 单元 */
adc_oneshot_unit_handle_t adc1_handle;
adc_oneshot_unit_init_cfg_t init_cfg = { .unit_id = ADC_UNIT_1 };
adc_oneshot_new_unit(&init_cfg, &adc1_handle);

/* 配置通道: 12dB衰减 → 满量程 ~3.3V */
adc_oneshot_chan_cfg_t chan_cfg = {
    .atten    = ADC_ATTEN_DB_12,
    .bitwidth = ADC_BITWIDTH_12,  /* 0~4095 */
};
adc_oneshot_config_channel(adc1_handle, ADC_CHANNEL_4, &chan_cfg);
```

### 4.4 算术平均滤波算法

1. 连续采集 **12 个** ADC 原始值
2. 排序（升序）
3. 去掉最大的 2 个和最小的 2 个
4. 对剩余 8 个取算数平均值

### 4.5 1-Wire 时序要求

> ⚠️ **关键时序**（≤ 100µs）必须使用关中断保证可靠性

```c
portDISABLE_INTERRUPTS();
// 关键时序操作
esp_rom_delay_us(40);
portENABLE_INTERRUPTS();
```

---

## 五、代码规范

### 5.1 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 变量/函数 | `snake_case` | `dht11_read()`, `photo_sensor_init()`, `mq135_is_warmed_up()` |
| 宏定义 | `UPPER_CASE` | `DHT11_DATA_GPIO`, `ADC_SAMPLE_COUNT` |
| 结构体 | `snake_case_t` | `dht11_data_t`, `photo_data_t` |
| 静态变量 | `s_<name>` | `s_mq135_warmed_up`, `s_adc1_inited` |
| 日志 TAG | 拼音或英文 | `"dht11"`, `"mq135"`, `"photo_sensor"` |

### 5.2 注释规范

```c
/**
 * @brief 函数功能简述
 *
 * 详细说明（来源依据、注意事项等）
 *
 * @param[out] data 参数说明
 * @return ESP_OK 成功, ESP_FAIL 失败
 */
```

### 5.3 数据来源追溯

任何与传感器参数相关的注释应标注来源：
```c
/* DATA[4] == DATA[0]+DATA[1]+DATA[2]+DATA[3] (DHT11 数据手册) */
/* GPIO20 对应 ADC1_CH4 (soc/esp32p4/include/soc/adc_channel.h) */
```

---

## 六、通信协议规范 ✅ 已实现

### 6.1 UDP 参数

| 参数 | 值 |
|------|-----|
| 目标 IP | `10.16.234.215` (2026-06-04 ipconfig 确认) |
| ESP32-P4 IP | DHCP 自动获取 |
| 目标端口 | `8080` (传感器 JSON) / `8082` (Camera JPEG 中继) |
| 发送间隔 | 每 2 秒 (传感器) / 每 ~333ms (Camera, 3fps) |
| 单包实际 | ~110 bytes (传感器), ≤4104 bytes (Camera 分包) |
| Socket API | lwip/sockets.h (BSD socket 兼容层) |
| Wi-Fi 等待 | `esp_netif_is_netif_up()` 轮询, 最多 20s (ESP-Hosted SDIO 需 ~13s) |
| ENOMEM 处理 | sendto errno=12 时 200ms 退避 × 3 次重试 |
| Camera 延迟 | 每包 5ms 微延迟, 防止 pbuf 池耗尽 |

### 6.2 JSON 报文格式

```json
{
  "type": "data",
  "level": 0,
  "ts": 12360,
  "dht11_t": 28.0,
  "dht11_h": 56.0,
  "ds18b20_t": 28.4375,
  "mq135_v": 0.31,
  "light_raw": 2000,
  "alert": 0,
  "err": 0,
  "reason": ""
}
```

| 字段 | 类型 | 精度 | 说明 |
|------|------|------|------|
| `type` | string | — | 报文类型, 固定 `"data"` |
| `level` | int | — | 报警级别: 0=正常, 1=预警, 2=严重, 3=紧急 |
| `ts` | int | 毫秒 | esp_timer_get_time() / 1000 |
| `dht11_t` | float | 1 位小数 | DHT11 温度 (°C) |
| `dht11_h` | float | 1 位小数 | DHT11 湿度 (%RH) |
| `ds18b20_t` | float | 4 位小数 | DS18B20 温度, 0.0625°C 分辨率 |
| `mq135_v` | float | 2 位小数 | MQ-135 AO 电压 (V) |
| `light_raw` | int | — | 光敏 ADC 原始值, photo_raw 直接上报 |
| `alert` | int | 0/1 | 任一 DO 为低 → 1 |
| `err` | int | 位掩码 | bit0=DHT11, bit1=DS18B20, bit2=MQ135, bit3=光敏 |
| `reason` | string | — | 报警原因枚举 (如 `"mq135"`, `"dht11_temp"`)

### 6.3 JSON 组包方式

> 使用 `snprintf()` 直接拼接，**不引入** cJSON 等第三方库（REQUIREMENT.md 6.3）

### 6.4 摄像头图像流协议 ✅ 已实现

> 详情见 `CAMERA_DEBUG_LOG.md`

| 参数 | 值 |
|------|-----|
| 摄像头 | KYT-U400 工业 USB UVC (PC直连) 或 ESP32-CAM (P4 HTTP中继) |
| 分辨率 / 格式 | 640×360 MJPG |
| 传输端口 | 8082 UDP (与传感器数据 8080 隔离) |
| 协议头 | Magic 0xAA55 + FrameID(2B) + ChunkIdx(2B) + TotalChunks(2B) |
| 单包载荷 | 1400 字节（避免分片） |
| 发送端 | `camera_http_fetch.c` (P4 HTTP 中继模式) 或 `camera_server.py` (Docker 直连) |
| 接收端 | `Docker camera_server.py` (UDP → 重组 → MJPEG) |

### 6.5 Camera HTTP Relay 配置 ✅ 已实现

| 参数 | 值 |
|------|-----|
| Kconfig 开关 | `CONFIG_CAMERA_HTTP_ENABLED` (默认 y) |
| ESP32-CAM URL | `CONFIG_CAMERA_HTTP_ESP32CAM_URL` (默认 `http://10.16.234.23/capture`) |
| 目标 IP | `CONFIG_CAMERA_HTTP_UDP_IP` (强制覆盖 `38.55.199.220`, 旧默认 `10.16.234.215`) |
| 帧率 | `CONFIG_CAMERA_HTTP_FPS` (1-10, 默认 3) |
| 缓冲区 | JPEG 128KB + UDP 64KB (PSRAM 分配) |
| HTTP 超时 | 5000ms |
| 依赖 | `esp_http_client` (IDF 内置, 无需额外 component) |

### 6.6 UDP 配置命令协议（v3.5）

**端口**：UDP 8081（与仿真注入共用）

**cmd:config — 报警配置命令**：

```json
{
  "cmd": "config",
  "mq135_alarm_src": 0,
  "photo_alarm_src": 1,
  "mq135_ao_dir": 0,
  "photo_ao_dir": 1,
  "mq135_ao_threshold": 2.5,
  "photo_ao_threshold": 1000,
  "dht11_temp_high": 35,
  "dht11_humi_high": 85,
  "ds18b20_temp_high": 35.0,
  "temp_humi_alarm_enabled": 1
}
```

所有字段均为可选。MCU 收到后：
1. 解析 JSON → 写入 `g_sensor_data` 运行时字段
2. `save_alarm_config_to_nvs()` 持久化到 NVS `alarm_cfg` 命名空间
3. 回复 `OK` 到发送方

**AO/DO 报警模式**：
- `ALARM_SRC_DO=0`：DO 数字量模式（硬件比较器，工厂预设阈值）
- `ALARM_SRC_AO=1`：AO 模拟量模式（软件阈值判定 + 触发方向）
- MQ-135 和光敏各自独立选择模式，温湿度可独立开关

### 6.7 报警升级定时 ✅ 已实现

| 参数 | 值 | 说明 |
|------|-----|------|
| `ALARM_ESCALATE_MS` | 30000ms | 报警持续超过 30s 自动升级到 L3 紧急 |



---

## 七、测试状态

### 7.1 已验证模块 ✅

| 模块 | 测试时间 | 结果 | 说明 |
|------|----------|------|------|
| DHT11 驱动 | 2026-05-27 | ✅ 通过 | 温湿度数据稳定，无校验错误 |
| 光敏电阻驱动 | 2026-05-27 | ✅ 通过 | ADC 读取正常，DO 检测正常 |
| DS18B20 驱动 | 2026-05-27 | ✅ 通过 | 温度数据稳定，精度 0.0625°C |
| MQ-135 驱动 | 2026-05-27 | ✅ 通过 | AO 电压读取正常，DO 报警正常 |
| OLED SSD1306 | 2026-06-01 | ✅ 通过 | I2C 通信稳定，显示清晰，无丢帧 |
| 蜂鸣器 | 2026-06-01 | ✅ 通过 | GPIO25 间歇鸣叫，报警逻辑正常 |
| MQ-135 预热 | 2026-06-04 | ✅ 通过 | 动态变量改为静态，避免 ESP-Hosted 冲突 |
| UDP 发送模块 | 2026-06-04 | ✅ 通过 | ESP32 → 10.16.234.215:8080, snprintf JSON, 2秒间隔 |
| pc_receiver.py | 2026-06-04 | ✅ 通过 | UDP 监听 + JSON 解析 + 控制台输出 + CSV 日志 |
| 端到端 WiFi 传输 | 2026-06-04 | ✅ 通过 | ESP32(10.16.234.86) → PC(10.16.234.215) 稳定传输 |
| Camera HTTP Relay | 2026-06-07 | ✅ 通过 | ESP32-CAM → HTTP → P4 → UDP:8082 → PC, 3fps 稳定 |
| ENOMEM 退避重试 | 2026-06-07 | ✅ 通过 | Camera/Sensor UDP 并发无 pbuf 耗尽, errno=12 自动恢复 |
| Wi-Fi 轮询等待 | 2026-06-07 | ✅ 通过 | esp_netif_is_netif_up() 替换固定 vTaskDelay, 适应 ESP-Hosted SDIO

### 7.2 已知 Bug 及修复

| Bug | 根因 | 修复 |
|-----|------|------|
| OLED 无显示 | `ssd1306_send_data()` 将控制字节 0x40 和数据分两次 I2C 事务发送，STOP 信号重置 SSD1306 状态机 | 合并为一次 I2C 事务 `[0x40, data...]` |
| 字体顶部缺失 | `fb_write_char()` 位掩码用 `0x3F`(6位) 而非 `0xFF`(8位)，丢失每列顶部 2 像素 | 改为 `0xFF` 完整字节掩码 |
| I2C 通信超时 | OLED 模块上拉电阻不足，内部上拉未开启 | `enable_internal_pullup: true` |
| OLED 仅第1行显示，其余乱码 | 单次 I2C 事务 1025 字节超 ESP32 TX FIFO(32B) 容量，数据丢失导致页面错乱 | 改用 Page Addressing Mode (0x20,0x02)，逐页发送 128 字节 × 8 次小事务 |
| ESP-Hosted Handler 多重进入崩溃 | `mq135_init()` 中访问 `g_sensor_data` 与 ESP-Hosted SDIO 中断冲突 | MQ-135 预热状态改用**静态变量** `s_mq135_warmed_up`，不访问共享结构 |
| MQ-135 上电误报警 | 预热期间 DO 输出不稳定 | 预热到 DO 连续 5 次为 1 或最大 60 秒超时，预热期间不参与报警 |
| Sensor UDP ENOMEM(errno=12) | Camera UDP 发送 4KB+ 大包耗尽 lwIP pbuf 池 | Sensor sendto 失败时 200ms 退避 × 3 次重试; Camera 每包 5ms 微延迟让路 |
| SO_SNDBUF errno=109 | lwIP UDP socket 不支持 SO_SNDBUF | 移除 setsockopt 调用, ENOMEM 退避已足够解决 |
| Camera HTTP "Host unreachable" | 固定 8s Wi-Fi 等待不足 (ESP-Hosted SDIO 需 ~13s) | 改用 esp_netif_is_netif_up() 轮询, 最多 20s |

---

## 八、待实现功能 📋

### 8.0 演示锁定机制 ✅ 2026-06-16

> 每浏览器独立 Cookie 锁，保护演示环境不被误操作。

| 组件 | 说明 |
|------|------|
| Cookie 签名 | `demo_unlock` Cookie 值 = HMAC-SHA256(salt, "unlocked") |
| 中间件 | `demo_lock_middleware` 拦截 POST/PUT/DELETE，ESP32 白名单放行 |
| 认证端点 | `POST /api/auth/unlock` (验证密码), `POST /api/auth/lock`, `GET /api/auth/status` |
| 前端守卫 | 14 个写操作函数均调用 `checkUnlock()` + 输错震屏动画 |
| 环境变量 | `DEMO_PASSWORD=111` (留空关闭锁定), `DEMO_SALT` (HMAC 盐) |

### 8.1 传感器驱动

- [x] **DS18B20** - 高精度温度传感器（0.0625°C分辨率）✅ 2026-05-27
- [x] **MQ-135** - 空气质量传感器（氨气、硫化物检测）✅ 2026-05-27
- [x] **MQ-135 预热检测** - 预热到 DO 稳定，避免误报警 ✅ 2026-06-04
- [x] **蜂鸣器报警** - 异常状态声光报警 ✅ 2026-06-01
- [x] **LED 报警** - 异常状态 LED 指示 ✅ 2026-06-05
- [x] **继电器控制** - 报警时控制风扇启动 ✅ 2026-06-05

### 8.2 数据显示

- [x] **OLED SSD1306 显示** - GPIO7(SDA)/GPIO8(SCL) I2C, 6x8字体, 8行, 1秒刷新 ✅ 2026-06-01

### 8.3 数据通信

- [x] **UDP Socket** - 建立 UDP 连接, lwip/sockets.h ✅ 2026-06-04
- [x] **JSON 组包** - snprintf() 按协议格式封装数据 ✅ 2026-06-04
- [x] **上位机脚本** - pc_receiver.py, UDP 监听 + CSV 日志 ✅ 2026-06-04

### 8.4 数据处理

- [x] **FreeRTOS 传感器任务** - 4个独立传感器任务（MQ-135/DS18B20/DHT11/光敏）✅ 2026-05-27
- [x] **FreeRTOS OLED 任务** - OLED 显示刷新任务（优先级2, 1秒刷新）✅ 2026-06-01
- [x] **互斥锁保护** - g_sensor_mutex 保护 sensor_shared_t ✅ 2026-06-01
- [x] **蜂鸣器报警任务** - 引用共享数据，间歇鸣叫（100ms/500ms）✅ 2026-06-01
- [x] **UDP 发送任务** - Task_UDP_Send (优先级2, 栈4096, Core 1, 每2秒) ✅ 2026-06-04

### 8.5 摄像头图像流

- [x] **摄像头采集** - OpenCV DirectShow 后端, 640×360 MJPG, 10fps ✅ 2026-06-06
- [x] **画质处理** - Gamma 0.55 提亮 + Sharpen 0.2 锐化 + JPEG q=80 ✅ 2026-06-06
- [x] **UDP 分包发送** - Magic 0xAA55 协议, 4096 字节/包 ✅ 2026-06-06
- [x] **接收显示** - 分片重组 + JPEG解码 + OpenCV imshow + FPS叠加 ✅ 2026-06-06
- [x] **调试文档** - CAMERA_DEBUG_LOG.md (8阶段调试历史 + 参数速查表) ✅ 2026-06-06

---

## 九、参考文档

### 9.1 项目文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 需求规格 | `REQUIREMENT.md` | 项目完整需求和技术参数 |
| ESP32-P4 手册 | `ESP32-P4-开发手册.md` | 开发板详细说明 |
| PSRAM 调试 | `PSRAM_DEBUG_GUIDE.md` | PSRAM 相关调试指南 |

### 9.2 传感器数据手册

| 传感器 | 文档路径 |
|--------|----------|
| DHT11 | `开发文档/【telesky旗舰店】DHT11 温湿度传感器通用/DHT11 数据手册.pdf` |
| DHT11 参考代码 | `开发文档/【telesky旗舰店】DHT11 温湿度传感器通用/参考代码/` |
| DS18B20 | `开发文档/DS18B20/DS18B20 数据手册（英文版）.pdf` |
| MQ-135 | `开发文档/MQ-135/技术手册.pdf` + `模块基础参数.pdf` |
| 光敏电阻 | `开发文档/光敏电阻传感器/光敏电阻传感器模块使用说明书.pdf` |

### 9.3 技术参考

| 主题 | 来源 |
|------|------|
| ADC 通道映射 | `F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\soc\esp32p4\include\soc\adc_channel.h` |
| ADC oneshot API | `esp_adc/include/esp_adc/adc_oneshot.h` |

---

## 十、开发注意事项

### 10.1 常见问题

1. **GPIO 引脚错误**
   - ❌ GPIO4、GPIO5 不是 ADC 引脚
   - ✅ ADC1 从 GPIO16 起步，排针上可用 GPIO20~23

2. **ADC API 版本**
   - ❌ 旧版 `adc1_config_width()` / `adc1_get_raw()` 已废弃
   - ✅ 使用 `adc_oneshot.h` 新版 API

3. **DHT11 上电等待**
   - ❌ 上电后立即读取会失败
   - ✅ 必须等待 ≥1 秒让传感器稳定

4. **MQ-135 预热**
   - ❌ 上电后立即使用 DO 报警会误报
   - ✅ 使用 `mq135_is_warmed_up()` 判断预热状态

5. **1-Wire 时序精度**
   - ❌ 直接使用 vTaskDelay 会导致时序不准
   - ✅ 关键时序使用 `portDISABLE_INTERRUPTS()`

6. **ESP-Hosted SDIO 中断冲突**
   - ❌ 在初始化阶段（如 `mq135_init()`）访问 `g_sensor_data` 可能导致 "Handler entered multiple times" 崩溃
   - ✅ 使用**静态变量**而非共享结构存储状态

### 10.2 硬件检查清单

- [ ] DHT11 DATA 引脚是否外接 5KΩ 上拉电阻？
- [ ] MQ-135 DO 输出是否经过电平转换（5V→3.3V）？
- [ ] 所有传感器是否共地？
- [ ] 电源电压是否符合传感器要求（3.3V 或 5V）？
- [ ] 蜂鸣器 S8050 基极是否串 1KΩ 限流电阻？

---

## 十一、更新记录

| 日期 | 更新内容 | 负责人 | 备注 |
|------|----------|--------|------|
| 2026-05-27 | 初始项目，建立框架 | Agent | 完成基础项目结构 |
| 2026-05-27 | 实现光敏电阻驱动 | Agent | 从 REQUIREMENT.md 移植 |
| 2026-05-27 | 实现 DHT11 驱动 | Agent | 从 51 参考代码移植，验证通过 |
| 2026-05-27 | 实现 DS18B20 驱动 | Agent | 从 51 参考代码移植，包含 1-Wire 协议 |
| 2026-05-27 | 实现 MQ-135 驱动 | Agent | 从 51 参考代码移植，ADC1_CH5 + DO 检测 |
| 2026-05-27 | 完成 DHT11 + 光敏电阻测试 | Agent | 验证通过 |
| 2026-06-01 | 新增 OLED SSD1306 显示 | Agent | GPIO7/GPIO8 I2C，共享数据结构体，OLED 显示任务 |
| 2026-06-01 | 修复 OLED 无显示 Bug | Agent | I2C 控制字节+数据分开发送；字体掩码 0x3F→0xFF；启用内部上拉 |
| 2026-06-01 | 实现蜂鸣器报警功能 | Agent | GPIO25 S8050 驱动，间歇鸣叫，OLED 状态显示 |
| 2026-06-01 | 修复 OLED 后续行乱码 | Agent | 切换为 Page Addressing Mode，逐页 128B 发送，避免 1025B 单次传输丢数据 |
| 2026-06-04 | MQ-135 预热功能 | Agent | 预热到 DO 稳定为 1，OLED 显示预热状态 |
| 2026-06-04 | 修复 ESP-Hosted 崩溃 | Agent | MQ-135 预热状态改为静态变量，避免 SDIO 中断冲突 |
| 2026-06-04 | UDP 发送模块 + 上位机脚本 | Agent | udp_sender.c/h + pc_receiver.py，端到端 WiFi 传输验证通过，IP 更新为 10.16.234.215 |
| 2026-06-05 | 新增 LED + 继电器驱动 | Agent | GPIO26(红灯)/GPIO27(绿灯) 共阴极双色LED，GPIO32 继电器风扇控制，集成到 buzzer 任务 |
| 2026-06-06 | 摄像头图像流模块 | Agent | KYT-U400 USB 摄像头, DirectShow + MJPG, Gamma/Sharpen 画质处理, UDP 分包, 接收显示, 8个阶段调试完成 |
| 2026-06-07 | Camera HTTP Relay | Agent | 新增 camera_http_fetch.c/h, ESP32-CAM HTTP 拉图 → UDP 0xAA55 分包转发, Kconfig 可配置, Task_Cam_HTTP 任务 |
| 2026-06-07 | UDP 稳定性修复 | Agent | ENOMEM(errno=12) 退避重试; Camera 每包 5ms 微延迟; 移除 lwIP 不支持的 SO_SNDBUF; Wi-Fi 等待改用 esp_netif 轮询 |
| 2026-06-08 | Docker Web 仪表盘 v3.0 上线 | Agent | FastAPI + WebSocket + AIOSQLite + Chart.js, 传感器卡片/报警历史/趋势曲线/仓储管理, Docker 容器化 |
| 2026-06-10 | 农资化肥场景迁移 v3.3 | Agent | 危化品 → 化肥场景全面迁移; QR 标签 FERT-xxx; 库存分类统计; generate_qr_labels.py --copies 参数 |
| 2026-06-12 | 摄像头架构重构 v3.3 | Agent | Docker 直连 ESP32-CAM (HVGA 480×320); Canvas 快照轮询替代 MJPEG <img>; CameraWebServer Arduino 工程 |
| 2026-06-13 | 仿真注入系统 v3.4 | Agent | 内置 UDP 监听器 SensorUDPProtocol:8080 替代外部桥接; POST /api/sim/inject + 前端预设面板; DELETE /api/alarms 报警清空; 库存管理增强 (手动新增/流水清空/物料删除); 视频流关闭→黑屏; 文档全面更新至 v3.4 |
| 2026-06-15 | 前端 MJPEG 解析修复 | Agent | 黑屏根因定位：服务端数据正常但前端 JS 两个 bug — (1) `\r\n--frameboundary`无法匹配流首裸boundary导致首帧跳过；(2) `buf.length-2`硬裁切在多帧共缓冲时夹带下一帧数据致JPEG损坏。修复：改用`--frameboundary`(15B)搜索 + 下一boundary精确定界帧尾。前端渲染方案为`fetch`→ReadableStream→boundary二进制切分→BlobURL; 替代了 v3.3的Canvas轮询和v3.5的`<img>`原生渲染。VGA_DEBUG_LOG.md 补充最终根因。PRODUCT_REPORT.md/README.md 同步前端渲染描述。**总结为 DEBUG_GUIDE.md 数据流分界实验法。** |
| 2026-06-16 | 演示锁定机制 v3.5 | Agent | HMAC-SHA256 Cookie 签名 + HTTP 中间件写操作拦截 + ESP32 白名单放行; 前端 🛡️锁定指示器 + 密码弹窗 + 14 个 checkUnlock() 守卫; DEMO_PASSWORD 环境变量控制开关; 每浏览器独立锁定 |\n| 2026-06-16 | 手机端响应式适配 | Agent | 900/600/480 三级断点; sim-panel 6→3→2列、cat-stats 4→2→1列; 弹窗 90vw+max-width; rawDataPanel min(420px,94vw); 表格 overflow-x滚动; 传感器字体 2rem→1.6→1.4rem; header/button/chart 逐级缩小; toast 全宽 |

---

> 📌 **提示**：后续 Agent 可以直接阅读本文档了解项目状态，无需从头探索代码库。
