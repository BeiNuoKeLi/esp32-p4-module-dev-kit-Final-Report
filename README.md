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
| 6 | 蜂鸣器状态 | `Buzzer: OFF` |
| 7 | 预留 | — |

- 每 **1 秒** 刷新，报警时蜂鸣器间歇鸣叫（100ms 鸣 / 500ms 停）

## 软件架构

```
4 × 传感器任务 (prio 3) ──→ sensor_shared_t ←── OLED 显示任务 (prio 2)
                                  ↑                   Buzzer 报警任务 (prio 2)
                            g_sensor_mutex
```

- **`sensors.c/h`**：DHT11 / DS18B20 / MQ-135 / 光敏 / 蜂鸣器 驱动
- **`oled_ssd1306.c/h`**：SSD1306 I2C 驱动，Page Addressing 逐页刷新
- **`hello_world_main.c`**：主入口，Wi-Fi STA（SDIO → ESP32-C6）

## 关键约束

- PSRAM 已禁用，代码不得依赖 `malloc` 外部分配
- MQ-135 上电预热 ≥ 3 分钟数据稳定
- DS18B20 12 位精度 0.0625°C，转换时间 ≥ 750ms
- OLED I2C 需 4.7KΩ 上拉电阻，已启用内部上拉
