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
| PSRAM | 当前已禁用（`CONFIG_SPIRAM=n`），代码中不得依赖 PSRAM |
| WiFi | 已通过 `esp_wifi_remote`（SDIO → ESP32-C6 协处理器）联网，`app_main()` 中已调用 `wifi_init_sta()` |
| 上位机 IP | `192.168.5.5`（Windows 局域网） |
| 通信端口 | `8080`（UDP） |

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
| 蜂鸣器+LED | 控制 | **GPIO 25** | 数字输出（需驱动电路） |

### 3.2 关键电路要求（来自文档）

| 项目 | 要求 | 数据手册依据 |
|------|------|-------------|
| DHT11 DATA 上拉 | 外接 **5KΩ** 电阻至 3.3V（线长 < 20m 时） | DHT11 数据手册 - 接口说明 |
| DS18B20 DATA 上拉 | 外接 **4.7KΩ** 电阻至 3.3V | DS18B20 数据手册 - 1-Wire 总线规范 |
| MQ-135 DO 电平转换 | 模块 5V 供电时 DO = 5V TTL，**必须经电平转换**降到 ≤3.3V（推荐 2KΩ:1KΩ 电阻分压） | 模块基础参数 - TTL 电平输出 |
| 光敏 DO 电平转换 | 同上 | 模块使用说明书 - 输出形式 |
| 蜂鸣器驱动 | GPIO 25 → 1KΩ → S8050 基极；集电极 → 有源蜂鸣器 → 3.3V；发射极 → GND | 标准三极管开关电路 |
| LED 限流 | 串 220Ω 限流电阻 | 标准 LED 驱动规范 |
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
| 检测气体 | 氨气、硫化物、苯系蒸气、烟雾 | 技术手册 - 产品描述 |
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

### 4.5 蜂鸣器 + LED 报警

- 使用有源蜂鸣器（通电即响），由 S8050 NPN 三极管驱动
- GPIO 25 输出高电平 → 蜂鸣器鸣叫 + LED 亮起
- **报警条件**：MQ-135 DO（GPIO22）为低电平，或光敏 DO（GPIO23）为低电平

---

## 五、软件架构规范

### 5.1 文件结构

```
main/
├── hello_world_main.c   # 已有：WiFi STA 初始化（wifi_init_sta），修改仅在 app_main() 末尾添加任务创建
├── sensors.h            # 新建：引脚宏、数据结构、函数声明
├── sensors.c            # 新建：所有传感器驱动 + 报警逻辑
├── udp_sender.h         # 新建：UDP 任务声明
└── udp_sender.c         # 新建：JSON 组包 + UDP Socket
```

### 5.2 FreeRTOS 任务设计

| 任务 | 优先级 | 栈大小 | 绑定核心 | 周期 |
|------|--------|--------|----------|------|
| `Task_SensorFetch` | 3 | 4096 | Core 0 | 每 2 秒 |
| `Task_UDP_Send` | 2 | 4096 | Core 1 | 事件驱动（`ulTaskNotifyTake`） |

### 5.3 任务间数据同步

```
               xSensorMutex (互斥锁)
Task_SensorFetch ──────────► sensor_data_t ◄────────── Task_UDP_Send
                              (共享结构体)
                              
Task_SensorFetch 写入完成后 → xTaskNotifyGive(Task_UDP_Send)
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

---

## 六、通信协议规范

### 6.1 UDP 发送参数

| 参数 | 值 |
|------|-----|
| 目标 IP | `192.168.5.5` |
| 目标端口 | `8080` |
| 发送间隔 | 每 2 秒（跟随采集周期） |
| 单包最大 | < 512 字节（避免 IP 分片） |

### 6.2 JSON 报文格式

```json
{
  "ts": 120000,
  "dht11_t": 26.0,
  "dht11_h": 62.0,
  "ds18b20_t": 28.3125,
  "mq135_v": 1.25,
  "light_v": 0.85,
  "alert": 0,
  "err": 0
}
```

| 字段 | 类型 | 精度 | 说明 |
|------|------|------|------|
| `ts` | int | 毫秒 | FreeRTOS 启动后时间戳 |
| `dht11_t` | float | 1 位小数 | DHT11 温度（°C），整数精度 |
| `dht11_h` | float | 1 位小数 | DHT11 湿度（%RH），整数精度 |
| `ds18b20_t` | float | **4 位小数** | DS18B20 高精度温度（0.0625°C 分辨率） |
| `mq135_v` | float | 2 位小数 | MQ-135 AO 电压（V） |
| `light_v` | float | 2 位小数 | 光敏 AO 电压（V） |
| `alert` | int | 0/1 | 0=正常，1=报警中（任一 DO 为低） |
| `err` | int | 位掩码 | bit0=DHT11, bit1=DS18B20, bit2=MQ135, bit3=光敏 |

### 6.3 JSON 组包方式

使用 `snprintf()` 直接拼接，不引入 cJSON 等第三方库：
```c
snprintf(buf, sizeof(buf),
    "{\"ts\":%lu,\"dht11_t\":%.1f,\"dht11_h\":%.1f,"
    "\"ds18b20_t\":%.4f,\"mq135_v\":%.2f,\"light_v\":%.2f,"
    "\"alert\":%d,\"err\":%d}",
    ts, dht11_t, dht11_h, ds18b20_t, mq135_v, light_v, alert, err);
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
[2026-05-21 14:30:02]  DHT11:  26.0°C |  62.0% || DS18B20: 28.3125°C || MQ135: 1.25V | Light: 0.85V || Alert: OK     | Err: 0x00
[2026-05-21 14:30:04]  DHT11:  26.0°C |  61.0% || DS18B20: 28.3750°C || MQ135: 1.30V | Light: 0.82V || Alert: OK     | Err: 0x00
[2026-05-21 14:30:06]  DHT11:  27.0°C |  60.0% || DS18B20: 28.4375°C || MQ135: 1.45V | Light: 0.79V || Alert: ALARM! | Err: 0x00
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
| 1 | `main/sensors.h` | 新建 | 引脚宏、`sensor_data_t` 结构体、函数声明 |
| 2 | `main/sensors.c` | 新建 | 全部传感器驱动 + ADC 滤波 + 报警逻辑 + 错误处理 |
| 3 | `main/udp_sender.h` | 新建 | UDP 任务声明 |
| 4 | `main/udp_sender.c` | 新建 | JSON 组包 + UDP Socket 发送 |
| 5 | `main/hello_world_main.c` | 修改 | 在 `app_main()` 末尾创建两个 FreeRTOS 任务 |
| 6 | `pc_receiver.py` | 新建 | Windows 上位机 Python 接收脚本 |

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

> **最后更新**：2026-05-21（引脚全面修正：GPIO4/5 改为 GPIO20/21；DHT11 → GPIO2，DS18B20 → GPIO1，MQ-135 AO → GPIO21/ADC1_CH5；废弃旧版 ADC API，改为 adc_oneshot API；根据微雪排针图核验所有引脚均在排针上）
>
> **基于文档**：`开发文档/` 下 DHT11、DS18B20、MQ-135、光敏电阻传感器 四个模块的全套资料
>
> **ADC 映射依据**：`F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\soc\esp32p4\include\soc\adc_channel.h`
