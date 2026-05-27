# ESP32-P4 开发手册 - Agent 使用指南

## 项目基本信息
- **目标芯片**: ESP32-P4
- **IDF 版本**: v5.5.1
- **ESP-IDF 根目录**: f:/BeiNuoKeLi/esp/esp-idf
- **工作目录**: f:/BeiNuoKeLi/esp/esp-idf
- **开发板**: 微雪 ESP32-P4-Module-DEV-KIT
- **文档位置**: f:/BeiNuoKeLi/esp/esp-idf/.trae/rules/ESP32-P4-开发手册.md

---

## 芯片规格

### 处理器系统
- **HP 系统**: 双核 RISC-V 32位，主频 360MHz，带 DSP 和 FPU
- **LP 系统**: 单核 RISC-V 32位，主频 40MHz
- **协处理器**: ESP32-C6，通过 SDIO 扩展 Wi-Fi 6 / Bluetooth 5

### 存储
- HP ROM: 128 KB
- LP ROM: 16 KB
- HP L2MEM: 768 KB
- LP SRAM: 32 KB
- TCM: 8 KB
- PSRAM: 32 MB (封装内)
- Nor Flash: 16 MB (模组集成)

---

## 关键目录路径（绝对路径）

### ESP-IDF 根目录
```
f:/BeiNuoKeLi/esp/esp-idf/
```

### 核心组件目录
```
f:/BeiNuoKeLi/esp/esp-idf/components/
```

### ESP32-P4 专属芯片支持
```
f:/BeiNuoKeLi/esp/esp-idf/components/soc/esp32p4/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_hw_support/port/esp32p4/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_rom/esp32p4/
f:/BeiNuoKeLi/esp/esp-idf/components/spi_flash/esp32p4/
```

### 独立驱动组件（v5.5.1 新架构）
```
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_gpio/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_i2c/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_spi/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_uart/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_i2s/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_adc/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_dac/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_ledc/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_pwm/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_gptimer/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_mcpwm/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_rmt/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_pcnt/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_twai/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_dma/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_touch_sens/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_jpeg/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_isp/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_cam/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_lcd/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_sdmmc/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_sdio/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_sdm/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_tsens/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_ana_cmpr/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_bitscrambler/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_cordic/
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_parlio/
```

### 显示与摄像头相关
```
f:/BeiNuoKeLi/esp/esp-idf/components/esp_lcd/
```

### 硬件抽象层
```
f:/BeiNuoKeLi/esp/esp-idf/components/hal/
f:/BeiNuoKeLi/esp/esp-idf/components/hal/include/hal/
```

### 示例代码
```
f:/BeiNuoKeLi/esp/esp-idf/examples/
f:/BeiNuoKeLi/esp/esp-idf/examples/peripherals/
f:/BeiNuoKeLi/esp/esp-idf/examples/peripherals/lcd/
f:/BeiNuoKeLi/esp/esp-idf/examples/peripherals/camera/
f:/BeiNuoKeLi/esp/esp-idf/examples/peripherals/usb/
f:/BeiNuoKeLi/esp/esp-idf/examples/system/ulp/
```

### 构建工具
```
f:/BeiNuoKeLi/esp/esp-idf/tools/cmake/
f:/BeiNuoKeLi/esp/esp-idf/tools/idf.py
```

---

## API 查找最佳策略（节省 Token）

### 1. 优先使用 SearchCodebase（最省 Token）
不要直接 Read 整个 .c 或 .h 文件，先用自然语言搜索：
- "ESP32-P4 MIPI DSI 初始化 API"
- "esp32p4 gpio 中断配置"
- "driver/i2c.h i2c_master_init 函数签名"

### 2. 只看 .h 头文件
如需确认函数签名，只读头文件，不读实现文件：
```
f:/BeiNuoKeLi/esp/esp-idf/components/driver/include/driver/*.h
f:/BeiNuoKeLi/esp/esp-idf/components/hal/include/hal/*.h
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_*/include/
```

### 3. 参考 examples
优先查找示例代码：
```
f:/BeiNuoKeLi/esp/esp-idf/examples/
```

---

## ESP32-P4 特有外设

| 外设 | 说明 | 相关组件 |
|---|---|---|
| MIPI-DSI | 高清显示屏接口 (2-lane) | esp_lcd, esp_driver_lcd |
| MIPI-CSI | 高清摄像头接口 (2-lane) | esp_driver_cam |
| ISP | 图像信号处理器 | esp_driver_isp |
| JPEG | 硬件编解码 (1080P@30fps) | esp_driver_jpeg |
| USB OTG 2.0 HS | 高速 USB | usb_host, usb_device |
| 以太网 | 百兆接口 | esp_eth |
| CORDIC | 数学协处理器 | esp_driver_cordic |
| SDIO 3.0 | 高速 SD 卡 | esp_driver_sdio, esp_driver_sdmmc |

---

## 开发板接口

### 主要接口
- MIPI-DSI (2-lane) 显示屏接口
- MIPI-CSI (2-lane) 摄像头接口
- 2×20 排针，28 个可编程 GPIO
- Type-A USB 2.0 OTG
- Type-C UART（烧录/调试）
- 百兆以太网 RJ45
- SDIO 3.0 SD 卡槽
- I2C / I3C 接口
- 板载麦克风
- 扬声器接口 (8Ω 2W)
- PoE 模块接口
- 5V 电源接口

### 按键
- BOOT：上电/复位时按下进入下载模式
- RST：复位

---

## 引脚定义参考

引脚图请参考微雪官方文档：  
https://docs.waveshare.net/ESP32-P4-Module-DEV-KIT/

---

## 环境配置

### 选择目标芯片
使用 idf.py set-target 配置：
```
f:/BeiNuoKeLi/esp/esp-idf/tools/idf.py set-target esp32p4
```

### 构建脚本
```
f:/BeiNuoKeLi/esp/esp-idf/tools/idf.py build
f:/BeiNuoKeLi/esp/esp-idf/tools/idf.py flash
f:/BeiNuoKeLi/esp/esp-idf/tools/idf.py monitor
```

---

## 关键 Kconfig 位置
```
f:/BeiNuoKeLi/esp/esp-idf/Kconfig
f:/BeiNuoKeLi/esp/esp-idf/components/esp_driver_*/Kconfig
f:/BeiNuoKeLi/esp/esp-idf/components/soc/esp32p4/Kconfig
```

---

## 重要提示

1. **不要使用 Arduino 框架**：当前 ESP32-P4 在 Arduino 平台支持有限，推荐使用 ESP-IDF v5.5.1
2. **搜索时带上 esp32p4**：确保搜索结果针对正确的芯片
3. **优先使用独立驱动**：v5.5.1 已将大部分外设从 driver/ 拆分到独立的 esp_driver_* 组件

---

## 本手册位置
```
f:/BeiNuoKeLi/esp/esp-idf/.trae/rules/ESP32-P4-开发手册.md
```
