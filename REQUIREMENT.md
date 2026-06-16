# 智能环境监测系统 — 需求规格与开发规范

> **适用于**：AI Agent 编写 ESP32-P4 边缘端代码 + Windows 上位机 Python 脚本
>
> **强制约束**：所有传感器驱动实现、ADC 配置参数、时序逻辑必须基于 `开发文档/` 目录下的数据手册和参考代码。**禁止凭记忆或猜测任何技术参数**。

---

## 一、项目概览

| 项目 | 说明 |
|------|------|
| 开发板 | 微雪(Waveshare) ESP32-P4-Module-DEV-KIT |
| 芯片 | ESP32-P4 (eco2, silicon v1.0, RISC-V 双核) |
| 框架 | ESP-IDF v5.5.1 |
| 编译器 | RISC-V 32-bit |
| PSRAM | 已启用 32MB（`CONFIG_SPIRAM=y`，200MHz 16线模式），详见 `PSRAM_DEBUG_GUIDE.md` |
| WiFi | 已通过 `esp_wifi_remote`（SDIO → ESP32-C6 协处理器）联网，`app_main()` 中已调用 `wifi_init_sta()` |
| 上位机 IP (本地) | `10.16.234.215`（Windows 局域网, 2026-06-04 ipconfig 确认） |
| 上位机 IP (VPS) | `38.55.199.220`（benoc.top, 2026-06-13 部署） |
| 通信端口 (本地) | `8080`（UDP） |
| 通信端口 (VPS) | `8002`（UDP, → 容器 :8080） |

---

## 二、文档资料来源（强制阅读）

**Agent 在编码前必须确认已阅读以下文档中的技术参数：**

| 传感器 | 文档路径 | 关键内容 |
|--------|----------|----------|
| DHT11 | `开发文档/【telesky旗舰店】DHT11 温湿度传感器通用/DHT11 温湿度传感器通用/DHT11 数据手册.pdf` | 精度表、供电电压、时序图、40bit 协议格式、上电稳定时间 |
| DHT11 参考代码 | `开发文档/【telesky旗舰店】DHT11 温湿度传感器通用/DHT11 温湿度传感器通用/参考代码/` | 51/STM32/Arduino 三平台的 DHT11 驱动实现 |
| DS18B20 | `开发文档/DS18B20/DS18B20 数据手册（英文版）.pdf` | 分辨率配置、温度换算公式、1-Wire ROM 命令、转换时间 |
| DS18B20 参考代码 | `开发文档/DS18B20/参考代码/` | One_Wire 初始化、读写时序、温度读取主逻辑 |
| MQ-135 | `开发文档/MQ-135/技术手册.pdf` | Rpbs/R0 灵敏度曲线、加热功耗、预热时间、基本测试电路 |
| MQ-135 | `开发文档/MQ-135/模块基础参数.pdf` | 模块供电、DO/AO 输出说明、TTL 低电平有效、电位器调节阈值 |
| MQ-135 参考代码 | `开发文档/MQ-135/参考代码/` | 51 平台 ADC 采集 + DO 检测逻辑 |
| 光敏电阻 | `开发文档/光敏电阻传感器/光敏电阻传感器模块使用说明书.pdf` | 模块规格、DO/AO 逻辑电平定义、LM393 比较器说明 |
| 光敏电阻 | `开发文档/光敏电阻传感器/光敏电阻传感器模块电路图.pdf` | 4 线制接线方式、AO/DO/VCC/GND 引脚定义 |
| 光敏电阻 参考代码 | `开发文档/光敏电阻传感器/传感器51测试程序/` | 51 平台 ADC 读取光敏值的实现 |

---

## 三、硬件接线规范

### 3.1 引脚分配（严格遵守）

> **ESP32-P4 ADC 通道映射**（来自 `soc/esp32p4/include/soc/adc_channel.h`）：
> ADC1 仅支持 GPIO16~GPIO23（CH0~CH7），ADC2 仅支持 GPIO49~GPIO54。
> GPIO4、GPIO5 **不是** ADC 引脚，已全部修正。
>
> **排针可用 GPIO**（根据微雪 ESP32-P4-Module-DEV-KIT 排针图）：
> GPIO0~6, 20~27, 32~33, 36, 45~48, 53, 54（GPIO54=SDIO Reset 不可用）
> GPIO7=SDA, GPIO8=SCL（I2C 保留），GPIO37/38=UART（保留）
>
> **ADC1 排针上可用的引脚**：GPIO20(CH4)、GPIO21(CH5)、GPIO22(CH6)、GPIO23(CH7)
> GPIO16~19 未引出到排针。

| 传感器 | 信号 | ESP32-P4 GPIO | 类型 |
|--------|------|---------------|------|
| DHT11 | DATA | **GPIO 2** | 数字 I/O（开漏，需上拉） |
| DS18B20 | DATA | **GPIO 1** | 数字 I/O（开漏，需上拉） |
| MQ-135 | AO | **GPIO 21** | ADC1_CH5（模拟输入，`ADC1_CHANNEL_5_GPIO_NUM=21`） |
| MQ-135 | DO | **GPIO 22** | 数字输入（需电平转换） |
| 光敏电阻 | AO | **GPIO 20** | ADC1_CH4（模拟输入，`ADC1_CHANNEL_4_GPIO_NUM=20`） |
| 光敏电阻 | DO | **GPIO 23** | 数字输入（需电平转换） |
| 蜂鸣器 | 控制 | **GPIO 25** | 数字输出（S8050 NPN 三极管驱动） ✅ 已实现 |
| LED（红色） | 阳极 | **GPIO 26** | 数字输出，共阴极双色LED，高电平触发点亮 |
| LED（绿色） | 阳极 | **GPIO 27** | 数字输出，共阴极双色LED，高电平触发点亮 |
| 继电器 | IN | **GPIO 32** | 数字输出，高电平触发闭合，控制风扇 |
| OLED SSD1306 | SDA | **GPIO 7** | I2C 数据线（需上拉 4.7KΩ） |
| OLED SSD1306 | SCL | **GPIO 8** | I2C 时钟线（需上拉 4.7KΩ） |

### 3.2 关键电路要求（来自文档）

| 项目 | 要求 | 数据手册依据 |
|------|------|-------------|
| DHT11 DATA 上拉 | 外接 **5KΩ** 电阻至 3.3V（线长 < 20m 时） | DHT11 数据手册 - 接口说明 |
| DS18B20 DATA 上拉 | 外接 **4.7KΩ** 电阻至 3.3V | DS18B20 数据手册 - 1-Wire 总线规范 |
| MQ-135 DO 电平转换 | 模块 5V 供电时 DO = 5V TTL，**必须经电平转换**降到 ≤3.3V（推荐 2KΩ:1KΩ 电阻分压） | 模块基础参数 - TTL 电平输出 |
| 光敏 DO 电平转换 | 同上 | 模块使用说明书 - 输出形式 |
| 蜂鸣器驱动 | GPIO 25 → 1KΩ → S8050 基极；集电极 → 有源蜂鸣器 → 3.3V；发射极 → GND | 标准三极管开关电路 |
| LED 红色限流 | GPIO 26 → 220Ω → 红色LED阳极；共阴极 → GND | 标准 LED 驱动规范 |
| LED 绿色限流 | GPIO 27 → 220Ω → 绿色LED阳极；共阴极 → GND | 标准 LED 驱动规范 |
| 继电器驱动 | GPIO 32 → 1KΩ → 继电器模块 IN；VCC 5V 供电；COM/NO 接风扇 | 标准继电器驱动电路 |
| MQ-135 预热 | 上电后 **预热 ≥ 3 分钟**读数稳定（裸传感器要求 48h，模块后缩短） | 技术手册 - 预热时间 |
| DHT11 上电稳定 | 上电后 **等待 ≥ 1 秒**越过不稳定状态 | DHT11 数据手册 - 电源引脚说明 |

---

## 四、传感器技术参数（来自数据手册，必须遵守）

### 4.1 DHT11 数字温湿度传感器

| 参数 | 值 | 来源 |
|------|-----|------|
| 供电电压 | 3.0V ~ 5.5V | 数据手册 - 电源引脚 |
| 温度范围 | 0 ~ 50°C | 数据手册 - 订货信息 |
| 温度精度 | **±2°C** | 数据手册 - 性能说明 |
| 温度分辨率 | **1°C（整数）** | 数据手册 - 性能说明 |
| 湿度范围 | 20% ~ 90%RH | 数据手册 - 订货信息 |
| 湿度精度 | **±5%RH**（25°C 时为 ±4%RH） | 数据手册 - 性能说明 |
| 湿度分辨率 | **1%RH（整数）** | 数据手册 - 性能说明 |
| 小数部分 | **恒为 0**（仅 DHT22 有小数） | 数据手册 - 数据格式 |
| 采样周期 | ≥ 2 秒 | 数据手册 - 应用说明 |
| 通信距离 | ≤ 20 米 | 数据手册 - 产品概述 |

**1-Wire 协议要点（来自参考代码 `DHT11.c`）：**
1. 主机拉低总线 **≥ 18ms**（参考代码使用 `Delay1ms(20)`）
2. 主机释放总线，拉高后等 40µs，检测 DHT11 响应
3. DHT11 响应：拉低 80µs → 拉高 80µs
4. 接收 40 bit（5 字节）：湿度整数 + 湿度小数 + 温度整数 + 温度小数 + **校验和**
5. 校验：`DATA[4] == DATA[0] + DATA[1] + DATA[2] + DATA[3]`
6. 数据位 `0`：50µs 低电平 + 26~28µs 高电平
7. 数据位 `1`：50µs 低电平 + 70µs 高电平

### 4.2 DS18B20 数字温度传感器

| 参数 | 值 | 来源 |
|------|-----|------|
| 供电电压 | 3.0V ~ 5.5V | 数据手册（英文版） |
| 温度范围 | **-55°C ~ +125°C** | 数据手册 - DESCRIPTION |
| 精度 | **±0.5°C**（-10°C ~ +85°C） | 数据手册 - DESCRIPTION |
| 分辨率 | 9/10/11/12 位可编程（默认 **12 位**） | 数据手册 - DESCRIPTION |
| 12 位精度 | **0.0625°C** | 数据手册 - 分辨率表 |
| 转换时间 | 12 位：最大 **750ms** | 数据手册 - 电气特性 |
| 64 位 ROM ID | 每片唯一 | 数据手册 - DESCRIPTION |

**1-Wire 操作流程（来自参考代码 `DS18B20.c` + `One_Wire.c`）：**
1. `One_Wire_Init()`：主机拉低 500µs → 释放 → 检测从机响应
2. `One_Wire_WriteData(0xCC)`：Skip ROM 命令
3. `One_Wire_WriteData(0x44)`：启动温度转换
4. 等待 ≥ 750ms
5. 再次 `One_Wire_Init()` + `0xCC` + `0xBE`（读暂存器）
6. 读 2 字节温度值：`Temp = (Htemp << 8 | Ltemp) / 16.0`
7. 负温度以补码表示

### 4.3 MQ-135 空气质量传感器

| 参数 | 值 | 来源 |
|------|-----|------|
| 模块供电 | **5V**（传感器加热） | 模块基础参数 - 工作电压 |
| 检测气体 | 氨气、硫化物、烟雾 | 技术手册 - 产品描述 |
| 检测浓度 | 10 ~ 1000 ppm | 技术手册 - 检测浓度 |
| 加热功耗 | ≤ 950mW | 技术手册 - 技术指标 |
| 加热电阻 | 30Ω ± 3Ω | 技术手册 - 技术指标 |
| AO 特性 | 浓度越高 → 电压越高 | 模块基础参数 - 特点 4 |
| DO 特性 | **TTL 低电平有效**（超阈值时 DO=0，信号灯亮） | 模块基础参数 - 特点 3 |
| DO 阈值 | 板上电位器可调 | 模块基础参数 - 功能简介 |
| 预热时间 | 上电后 ≥ 3 分钟 | 技术手册 - 注意事项 |

**传感器电阻计算公式（来自技术手册 - 基本电路）：**
```
Rs = (Vc / Vout - 1) × RL
```
- Vc = 回路电压（5.0V）
- Vout = AO 引脚电压（ADC 读数换算）
- RL = 负载电阻（板上默认值，通常 1KΩ）

### 4.4 光敏电阻传感器模块（4 线制）

| 参数 | 值 | 来源 |
|------|-----|------|
| 工作电压 | 3.3V ~ 5V | 使用说明书 - 模块特色 |
| 比较器芯片 | **LM393** | 使用说明书 - 模块特色 |
| AO 特性 | 光照越强 → 电压越高 | 使用说明书 - 模块使用说明 5 |
| DO 特性 | **低于阈值 → 高电平，超过阈值 → 低电平** | 使用说明书 - 模块使用说明 2 |
| DO 阈值 | 板上电位器可调 | 使用说明书 - 模块使用说明 3 |
| 输出驱动能力 | 超过 15mA | 使用说明书 - 模块特色 |

### 4.5 蜂鸣器 + LED 报警 + 继电器控制

#### 蜂鸣器
- 使用有源蜂鸣器（通电即响），由 S8050 NPN 三极管驱动
- GPIO 25 输出高电平 → 蜂鸣器鸣叫
- **报警条件**：MQ-135 DO（GPIO22）为低电平，或光敏 DO（GPIO23）为低电平
- **鸣叫模式**：间歇 100ms 鸣叫 / 500ms 静音，避免持续噪音

#### 双色 LED（共阴极）
- 公共阴极已外接 GND，红色阳极 → GPIO 26，绿色阳极 → GPIO 27
- 各串 220Ω 限流电阻，高电平触发点亮
- **正常状态**：红灯灭（GPIO26=0），绿灯亮（GPIO27=1）
- **报警状态**：红灯亮（GPIO26=1），绿灯灭（GPIO27=0）

#### 继电器控制风扇
- GPIO 32 输出高电平 → 继电器闭合 → 风扇启动
- 串 1KΩ 限流电阻至继电器模块 IN 引脚
- **正常状态**：GPIO32=1，继电器闭合，风扇运转
- **报警状态**：GPIO32=0，继电器断开，风扇停止

---

## 五、软件架构规范

### 5.1 文件结构

```
main/
├── smart_monitor_main.c   # 已有：WiFi STA 初始化 + 传感器/OLED/报警任务创建
├── sensors.h            # 已有：引脚宏、传感器数据结构、函数声明
├── sensors.c            # 已有：全部传感器驱动 + ADC 滤波 + 错误处理
├── oled_ssd1306.h       # 已有：SSD1306 OLED I2C 驱动头文件
├── oled_ssd1306.c       # 已有：SSD1306 OLED framebuffer 渲染 + 6x8 字体
├── udp_sender.h         # 新建：UDP 任务声明
├── udp_sender.c         # 新建：JSON 组包 + UDP Socket
├── sim_poll.h           # 新建：仿真注入 & 报警配置 HTTP 轮询声明
└── sim_poll.c           # 新建：HTTP GET 轮询 /api/sim/poll + /api/alarm/config/poll
```

### 5.2 FreeRTOS 任务设计

| 任务 | 优先级 | 栈大小 | 绑定核心 | 周期 | 说明 |
|------|--------|--------|----------|------|------|
| `mq135_sensor` | 3 | 4096 | 自动 | 每 2 秒 | |
| `ds18b20_sensor` | 3 | 4096 | 自动 | 每 3 秒 | |
| `dht11_sensor` | 3 | 4096 | 自动 | 每 2 秒 | |
| `photo_sensor` | 3 | 4096 | 自动 | 每 2 秒 | |
| `oled_display` | 2 | 4096 | 自动 | 每 1 秒 | |
| `buzzer_alarm` | 2 | 5120 | 自动 | 每 500ms | 含 LED + 继电器控制 |
| `Task_UDP_Send` | 2 | 5120 | Core 1 | 每 2s | 传感器数据上报 |
| `Task_UDP_Sim` | 1 | 4096 | 自动 | 事件驱动 | 局域网仿真命令监听 8081 |
| `Task_Sim_Poll` | 1 | 8192 | 自动 | 每 3s (仿真) + 10s (配置) | 公网 VPS HTTP 轮询 |

### 5.3 任务间数据同步

```
                  g_sensor_mutex (互斥锁)
4 传感器任务 ──────────► sensor_shared_t ◄────────── oled_display 任务
                              ↓
                        buzzer_alarm 任务 ← 读取 mq135_do / photo_do
                              ↓
                        Task_UDP_Send (待实现)
```

### 5.4 ADC 配置（来自 ESP-IDF v5.x oneshot API）

> ⚠️ `adc1_config_width()` / `adc1_get_raw()` 等旧版 API 在 IDF v5.x 中已废弃，
> 应使用 `esp_adc/adc_oneshot.h` 中的新 API。

```c
/* 初始化 ADC1 单元 */
adc_oneshot_unit_handle_t adc1_handle;
adc_oneshot_unit_init_cfg_t init_cfg = { .unit_id = ADC_UNIT_1 };
adc_oneshot_new_unit(&init_cfg, &adc1_handle);

/* 配置通道：ADC_ATTEN_DB_12 满量程 ~3.3V（等价旧版 ADC_ATTEN_DB_11） */
adc_oneshot_chan_cfg_t chan_cfg = {
    .atten    = ADC_ATTEN_DB_12,   // hal/include/hal/adc_types.h:50
    .bitwidth = ADC_BITWIDTH_12,   // 12位，0~4095
};
adc_oneshot_config_channel(adc1_handle, ADC_CHANNEL_5, &chan_cfg); // GPIO21, MQ-135 AO
adc_oneshot_config_channel(adc1_handle, ADC_CHANNEL_4, &chan_cfg); // GPIO20, 光敏 AO

/* 读取原始值 */
int raw = 0;
adc_oneshot_read(adc1_handle, ADC_CHANNEL_5, &raw); // MQ-135 AO
adc_oneshot_read(adc1_handle, ADC_CHANNEL_4, &raw); // 光敏 AO
```

电压换算：`voltage = adc_reading * 3.3f / 4095.0f`

### 5.5 算术平均滤波算法

对 MQ-135 AO 和光敏 AO 分别实施：
1. 连续采集 **12 个** ADC 原始值
2. 排序（冒泡或快排）
3. 去掉最大的 2 个和最小的 2 个
4. 对剩余 8 个取算数平均值
5. 换算为电压值

### 5.6 1-Wire 在 ESP32 FreeRTOS 下的可靠实现

由于 FreeRTOS 任务调度可能打断微秒级延迟，**关键时序区段**（≤ 100µs）必须使用：
```c
portDISABLE_INTERRUPTS();
// 关键时序操作
portENABLE_INTERRUPTS();
```

DHT11 和 DS18B20 使用不同的 GPIO，需分别实现驱动函数，不可混用。

### 5.7 传感器错误处理

| 错误 | 检测方式 | 处理 |
|------|----------|------|
| DHT11 校验失败 | `DATA[4] != DATA[0]+DATA[1]+DATA[2]+DATA[3]` | 重试 1 次，仍失败用上次有效值，`err \|= 0x01` |
| DS18B20 无响应 | `One_Wire_Init()` 返回非 0 | 重试 1 次，仍失败用上次有效值，`err \|= 0x02` |
| MQ-135 ADC 异常 | 12 次采样全部为 0 或全部为 4095 | `err \|= 0x04` |
| 光敏 ADC 异常 | 同上 | `err \|= 0x08` |

### 5.5 运行时报警配置（v3.5 — AO/DO 双模式）

系统支持在运行时动态切换每个传感器的报警判定模式，无需重新编译或重启：

**报警源选择枚举（`alarm_source_t`）**：

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | `ALARM_SRC_DO` | **DO 数字量模式**：硬件比较器判定（工厂预设阈值），0=超阈值/1=正常 |
| 1 | `ALARM_SRC_AO` | **AO 模拟量模式**：软件阈值判定，需配合 `ao_dir` + `ao_threshold` |

**AO 触发方向（`ao_trigger_dir_t`）**：

| 值 | 名称 | 适用传感器 | 含义 |
|----|------|-----------|------|
| 0 | `AO_TRIG_ABOVE` | MQ-135 | ADC raw 值 ≥ 阈值 → 报警（有毒气体浓度过高） |
| 1 | `AO_TRIG_BELOW` | 光敏 | ADC ≤ 阈值 → 报警（光线过暗/遮挡） |

**可配置参数列表**：

| 参数 | 类型 | 默认值 | NVS Key | 说明 |
|------|------|--------|---------|------|
| `mq135_alarm_src` | int | 0 (DO) | `mq_mode` | MQ-135 报警源 |
| `photo_alarm_src` | int | 0 (DO) | `ph_mode` | 光敏报警源 |
| `mq135_ao_dir` | int | 0 (ABOVE) | `mq_ao_dir` | MQ-135 AO 触发方向 |
| `photo_ao_dir` | int | 1 (BELOW) | `ph_ao_dir` | 光敏 AO 触发方向 |
| `mq135_ao_threshold` | int (ADC raw) | 3100 | `mq_ao_raw` | MQ-135 AO ADC 阈值 (0~4095) |
| `photo_ao_threshold` | int (ADC) | 1000 | `ph_ao_thr` | 光敏 AO ADC 阈值 |
| `dht11_temp_high` | int (°C) | 35 | `dht_t_hi` | DHT11 高温阈值 |
| `dht11_humi_high` | int (%RH) | 85 | `dht_h_hi` | DHT11 高湿阈值 |
| `ds18b20_temp_high` | float (°C) | 35.0 | `ds_t_hi` | DS18B20 高温阈值 |
| `temp_humi_alarm_enabled` | int | 1 | `temp_en` | 温湿度报警总开关 (1=启用, 0=关闭) |

**NVS 持久化**：
- 命名空间 `alarm_cfg`，使用 ESP-IDF NVS API
- 启动时 `load_alarm_config_from_nvs()` 加载 → 写入 `g_sensor_data` 运行时结构体
- 收到 config 命令后 `save_alarm_config_to_nvs()` 立即写回 NVS
- 加载时进行**范围校验**（如 dht_t_hi 必须在 10~60°C），拒绝垃圾值并回退默认值

**配置下发链路**：
```
Web 仪表盘 POST /api/alarm/config → Docker SQLite 镜像
     ├─ UDP "cmd:config" → ESP32 UDP 8081 (快速路径, 局域网可达)
     │     └─ udp_sim_command_task() 解析 → 写入 g_sensor_data → save_alarm_config_to_nvs()
     │
     └─ 写入 _pending_alarm_cfg 暂存区 → ESP32 HTTP GET /api/alarm/config/poll?seq=N (可靠路径, NAT 穿透)
           └─ sim_poll_task() 每 10s 轮询 → apply_alarm_config() → save_alarm_config_to_nvs() 持久化
```
- **v3.7 启动同步**：seq=0 且无待下发配置时，VPS 从 SQLite 返回当前完整配置作为初始同步，解决 Docker 重启后 `_pending_alarm_cfg` 内存队列丢失导致 ESP32 拿不到配置的问题。

### 5.6 报警升级定时

| 参数 | 值 | 说明 |
|------|-----|------|
| `ALARM_ESCALATE_MS` | 30000 (30s) | 报警持续超过此时长，自动升级到 L3 紧急级别 |

---

## 六、通信协议规范

### 6.1 UDP 发送参数

| 参数 | 值 |
|------|-----|
| 目标 IP (本地) | `10.16.234.215` |
| 目标 IP (VPS) | `38.55.199.220` |
| 目标端口 (本地) | `8080` |
| 目标端口 (VPS) | `8002` |
| 发送间隔 | 每 2 秒（跟随采集周期） |
| 单包最大 | < 512 字节（避免 IP 分片） |

### 6.1.2 UDP 配置命令（v3.5）

MCU 监听 **UDP 8081** 接收仿真注入和报警配置命令。

**命令类型**：

| cmd 值 | 方向 | 说明 |
|--------|------|------|
| `config` | Docker → ESP32 | 下发报警配置 |
| `reset` | Docker → ESP32 | 退出仿真模式 |

**config 命令 JSON 格式**：

```json
{
  "cmd": "config",
  "mq135_alarm_src": 0,
  "photo_alarm_src": 1,
  "mq135_ao_dir": 0,
  "photo_ao_dir": 1,
  "mq135_ao_threshold": 3100,
  "photo_ao_threshold": 1000,
  "dht11_temp_high": 35,
  "dht11_humi_high": 85,
  "ds18b20_temp_high": 35.0,
  "temp_humi_alarm_enabled": 1
}
```

所有字段均为可选，缺失字段 MCU 保留当前值不变。

### 6.2 JSON 报文格式

```json
{
  "type": "data",
  "level": 0,
  "ts": 120000,
  "dht11_t": 26.0,
  "dht11_h": 62.0,
  "ds18b20_t": 28.3125,
  "mq135_v": 1.25,
  "mq135_raw": 1551,
  "light_raw": 1500,
  "mq135_do": 1,
  "photo_do": 1,
  "alert": 0,
  "err": 0,
  "reason": ""
}
```

| 字段 | 类型 | 精度 | 说明 |
|------|------|------|------|
| `type` | string | — | 消息类型，固定 `"data"` |
| `level` | int | — | 报警级别 (0=正常, 1=预警, 2=严重, 3=紧急) |
| `ts` | int | 毫秒 | FreeRTOS 启动后时间戳 |
| `dht11_t` | float | 1 位小数 | DHT11 温度（°C），整数精度 |
| `dht11_h` | float | 1 位小数 | DHT11 湿度（%RH），整数精度 |
| `ds18b20_t` | float | **4 位小数** | DS18B20 高精度温度（0.0625°C 分辨率） |
| `mq135_v` | float | 2 位小数 | MQ-135 AO 电压（V），向后兼容 |
| `mq135_raw` | int | — | **MQ-135 ADC 原始值（0~4095）**，与光敏统一单位 |
| `light_raw` | int | — | 光敏 ADC 原始值（0~4095） |
| `mq135_do` | int | 0/1 | MQ-135 DO 数字量状态（0=报警，1=正常） |
| `photo_do` | int | 0/1 | 光敏 DO 数字量状态（0=报警，1=正常） |
| `alert` | int | 0/1 | 0=正常，1=报警中（任一报警源触发） |
| `err` | int | 位掩码 | bit0=DHT11, bit1=DS18B20, bit2=MQ135, bit3=光敏 |
| `reason` | string | — | 触发原因 (mq135/dht11_temp/ds18b20_temp/photo/dht11_humi) |

### 6.3 JSON 组包方式

使用 `snprintf()` 直接拼接，不引入 cJSON 等第三方库：
```c
snprintf(buf, sizeof(buf),
    "{\"type\":\"data\",\"level\":%d,"
    "\"ts\":%lu,\"dht11_t\":%.1f,\"dht11_h\":%.1f,"
    "\"ds18b20_t\":%.4f,\"mq135_v\":%.2f,\"mq135_raw\":%d,\"light_raw\":%d,"
    "\"mq135_do\":%d,\"photo_do\":%d,"
    "\"alert\":%d,\"err\":%d,\"reason\":\"%s\"}",
    level, ts, dht11_t, dht11_h, ds18b20_t, mq135_v, mq135_raw, light_raw,
    mq135_do, photo_do, alert, err, reason);
```

---

## 七、上位机 Python 接收脚本规范

### 7.1 功能要求

| 功能 | 说明 |
|------|------|
| UDP 监听 | `socket.SOCK_DGRAM` 绑定 `0.0.0.0:8080` |
| JSON 解析 | `json.loads()` 解析报文 |
| 控制台输出 | 带本地时间戳 `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` |
| 报警高亮 | `alert==1` 时使用 `\033[91m` 红色标记 |
| CSV 日志 | 追加写入 `sensor_log.csv`，首次运行时写入表头 |
| 优雅退出 | 捕获 `KeyboardInterrupt`（Ctrl+C），关闭 socket |

### 7.2 输出格式示例

```
============================================================
  智能环境监测系统 - 上位机接收端
  监听端口: 8080
============================================================
[2026-05-21 14:30:02]  DHT11:  26.0°C |  62.0% || DS18B20: 28.3125°C || MQ135: 1551 raw (1.25V) | Light: 1500 raw || Alert: OK     | Err: 0x00
[2026-05-21 14:30:04]  DHT11:  26.0°C |  61.0% || DS18B20: 28.3750°C || MQ135: 1613 raw (1.30V) | Light: 1450 raw || Alert: OK     | Err: 0x00
[2026-05-21 14:30:06]  DHT11:  27.0°C |  60.0% || DS18B20: 28.4375°C || MQ135: 1800 raw (1.45V) | Light: 1380 raw || Alert: ALARM! | Err: 0x00
```

### 7.3 依赖

仅标准库：`socket`, `json`, `datetime`, `csv`, `os`，无需 `pip install`。

---

## 八、代码质量要求

1. **中文注释**：每个函数、每个关键逻辑块必须有中文注释
2. **命名规范**：遵循 ESP-IDF 风格（`snake_case`），宏定义用 `UPPER_CASE`
3. **错误检查**：所有 ESP-IDF API 返回值使用 `ESP_ERROR_CHECK()`
4. **日志输出**：使用 `ESP_LOGI(TAG, ...)` / `ESP_LOGW(TAG, ...)` / `ESP_LOGE(TAG, ...)`
5. **无 warning**：编译应零警告（`-Wall -Werror`）
6. **数据来源可追溯**：任何与传感器参数相关的注释应标注"数据手册 p.X" 或 "参考代码 xxx.c:LINE"

---

## 九、交付物清单

| # | 文件 | 类型 | 说明 |
|---|------|------|------|
| 1 | `main/sensors.h` | 已有 | 引脚宏、`sensor_data_t` 结构体、函数声明 |
| 2 | `main/sensors.c` | 已有 | 全部传感器驱动 + ADC 滤波 + 报警逻辑 + 错误处理 |
| 3 | `main/oled_ssd1306.h` | 已有 | SSD1306 OLED I2C 驱动头文件 |
| 4 | `main/oled_ssd1306.c` | 已有 | SSD1306 OLED framebuffer 渲染 + 6x8 字体 |
| 5 | `main/udp_sender.h` | 新建 | UDP 任务声明 |
| 6 | `main/udp_sender.c` | 新建 | JSON 组包 + UDP Socket 发送 |
| 7 | `main/smart_monitor_main.c` | 已有 | 传感器任务 + OLED 显示任务创建 |
| 8 | `pc_receiver.py` | 新建 | Windows 上位机 Python 接收脚本 |

---

## 十、参考代码移植指南

Agent 在编写驱动时，应将开发文档中的 **51 单片机参考代码** 移植到 ESP-IDF 框架：

| 51 代码 | ESP-IDF 等价替换 |
|---------|------------------|
| `sbit DQ = P2^0` | `gpio_set_direction(GPIO_NUM_2, GPIO_MODE_INPUT_OUTPUT_OD)` （DHT11, GPIO2） |
| `sbit DQ = P3^7` | `gpio_set_direction(GPIO_NUM_1, GPIO_MODE_INPUT_OUTPUT_OD)` （DS18B20, GPIO1） |
| `DQ = 0; DQ = 1` | `gpio_set_level(GPIO_NUM_x, 0)` / `gpio_set_level(GPIO_NUM_x, 1)` |
| `if(DQ) ...` | `gpio_get_level(GPIO_NUM_x)` |
| `Delay1ms(x)` | `vTaskDelay(pdMS_TO_TICKS(x))`（毫秒级） |
| `Delay40us()` | `portDISABLE_INTERRUPTS()` + `esp_rom_delay_us(40)` + `portENABLE_INTERRUPTS()` |
| `Uart_TxData(c)` | `printf()` 或 `ESP_LOGI()` |
| 51 PCF8591 ADC 读取 | `adc_oneshot_read(handle, ADC_CHANNEL_5, &raw)` （MQ-135, GPIO21） |
| 51 PCF8591 ADC 读取 | `adc_oneshot_read(handle, ADC_CHANNEL_4, &raw)` （光敏, GPIO20） |

---

## 十一、农资化肥仓储业务场景与待实现功能

> **更新日期**：2026-06-13
>
> **核心目标**：完成 Web 可视化仪表盘（v3.0），实现 Docker 容器化部署，提供实时数据展示与仓储管理功能。
>
> **架构说明**：本章内容是对原有设计的**增量升级**，保持 UDP 点对点通信不变，新增 Docker 服务层（FastAPI + WebSocket + AIOSQLite）。

---

### 11.1 业务场景描述

系统服务于**农资化肥仓储**场景，依赖"一套环境传感器"和"一个摄像头"，在不同的业务节点上各司其职。

#### 场景一：日常状态 — 环境静默监测
- **功能逻辑**：系统在后台默默地刷新仓库内的温度、湿度以及空气质量
- **业务价值**：确保化肥一直处于合规、安全的存放环境中

#### 场景二：物料入库 — 身份与环境的"瞬间绑定"
- **操作流程**：工人推着一袋贴有二维码的化肥进入仓库工位
- **功能逻辑**：
  1. 摄像头此时充当"扫码枪"，自动识别出物料的身份 ID
  2. 识别成功的刹那，系统立刻读取当前的温湿度和空气数据
  3. 将"物料身份"和"入库时的环境状态"打包上传
- **业务价值**：实现**安全溯源**，未来可查询"该物料于某日入库，入库时现场温度 24°C，空气指标完全正常"

#### 场景三：突发异常 — 拍照存证与应急联动
- **操作流程**：仓库内突然发生次生灾害（化肥分解泄漏、氨气扩散），导致有害气体浓度瞬间超标
- **功能逻辑**：
  1. 传感器检测到数据越线，立刻拉响**最高级别警报**
  2. 摄像头角色瞬间转换，从"扫码枪"变成"行车记录仪"，对着现场"咔嚓"抓拍一张高清事故照片，并立刻传回上位机
  3. 与此同时，现场的应急设备（如排风扇、蜂鸣器）被**强制直接开启**
- **业务价值**：提供**视觉存证**与**自动化防御**

---

### 11.2 上位机功能扩展（基于现有 UDP 架构）

在原有 Python 接收脚本基础上进行功能扩展：

| 功能 | 说明 | 扩展方式 |
|------|------|----------|
| 入库记录 | 接收物料入库事件，追加写入 `checkin_log.csv` | 新增消息类型 `type=checkin` |
| 报警拍照 | 接收异常抓拍的图片文件，保存到本地 | 新增消息类型 `type=alert_image` |
| Web 展示 | 本地启动轻量 Web 服务器，浏览器查看实时数据曲线 | Flask/fastapi 集成 |
| 报警弹窗 | 浏览器页面在异常时红色闪烁 + 弹出图片 | JavaScript WebSocket |

---

### 11.3 功能实现步骤清单

#### 阶段一：网络通信扩展 ✅ 已完成
- [x] 1.1 启用 Wi-Fi STA，连接局域网热点（ESP32-P4 自己联网，不再经过 ESP32-C6）
- [x] 1.2 扩展 JSON 协议字段（新增 `type` 字段区分消息类型：`data` / `checkin` / `alert_image`）
- [x] 1.3 实现 UDP 图片分包传输（图片大于 512 字节时分包发送，Magic 0xAA55 协议）

#### 阶段二：摄像头功能 ✅ 已完成
- [x] 2.1 ~~ESP32-CAM HTTP 拉流 + UDP 中继转发（camera_http_fetch.c）~~ **已删除**（2026-06-16，P4 不再参与摄像头中继）
- [x] 2.2 二维码识别功能（pyzbar 扫码枪模式）
- [x] 2.3 ~~工业摄像头 KYT-U400 支持（camera_capture_sender.py）~~ **已弃用**
- [x] 2.4 独立预览窗口（Toplevel 640×480）

#### 阶段三：业务逻辑开发 ✅ 已完成
- [x] 3.1 **入库流程**：扫码成功 → 记录物料ID → 绑定当前环境数据 → SQLite 持久化
- [x] 3.2 **出库流程**：扫码验证 → 记录出库时间 → 更新库存状态
- [x] 3.3 **分级报警逻辑**：L0(正常)/L1(预警)/L2(严重)/L3(紧急)

#### 阶段四：Web 仪表盘（v3.0）✅ 已完成
- [x] 4.1 FastAPI REST API（传感器数据、库存管理、摄像头控制）
- [x] 4.2 WebSocket 实时推送（传感器数据即时更新）
- [x] 4.3 Chart.js 历史趋势曲线（温度/湿度）
- [x] 4.4 AIOSQLite 异步数据库存储
- [x] 4.5 Docker 容器化部署（docker-compose.yml）
- [x] 4.6 报警时浏览器红色闪烁（L3 紧急级别）
- [x] 4.7 **内置 UDP 监听器**（SensorUDPProtocol:8080，替代外部桥接）
- [x] 4.8 **仿真注入系统**（POST /api/sim/inject，前端 L1/L2/L3 预设）
- [x] 4.9 **报警管理增强**（一键清空 DELETE /api/alarms）
- [x] 4.10 **库存管理增强**（手动新增 + 分类统计 + 流水清空 + 物料删除）

#### 阶段五：多仓库节点集中管理平台 ⏳ 规划中
- [ ] 5.1 多 ESP32-P4 节点接入
- [ ] 5.2 节点状态监控面板
- [ ] 5.3 跨节点数据汇总分析
- [ ] 5.4 告警通知（邮件/短信）

---

### 11.4 创新点总结

> **一句话总结**：
> 它用**最少的硬件（传感器 + 一个摄像头）**，既解决了化肥"是谁、什么时候进来的、进来时环境好不好"的**溯源问题**；又解决了危险发生时"现场到底发生了什么、怎么自救"的**报警与留痕问题**。思路非常闭环！

---

> **最后更新**：2026-06-13（v3.4 仿真注入上线；内置 UDP 监听器；报警/库存管理增强；化肥场景全面迁移）
>
> **基于文档**：`开发文档/` 下 DHT11、DS18B20、MQ-135、光敏电阻传感器 四个模块的全套资料
>
> **ADC 映射依据**：`F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\soc\esp32p4\include\soc\adc_channel.h`
