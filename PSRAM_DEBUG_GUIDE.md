# ESP32-P4 PSRAM 配置指南

## 配置完成状态

✅ **已成功配置并验证**

## 最终稳定配置

```ini
# ==================== PSRAM 最佳配置 ====================
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_HEX=y
CONFIG_SPIRAM_SPEED_200M=y
CONFIG_SPIRAM_SPEED=200
CONFIG_SPIRAM_BOOT_HW_INIT=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION=y
CONFIG_SPIRAM_MEMTEST=y
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y
CONFIG_IDF_EXPERIMENTAL_FEATURES=y

# 禁用高风险功能
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set
# CONFIG_SPIRAM_FETCH_INSTRUCTIONS is not set
# CONFIG_SPIRAM_RODATA is not set
# CONFIG_SPIRAM_ECC_ENABLE is not set
# CONFIG_SPIRAM_ALLOW_NOINIT_SEG_EXTERNAL_MEMORY is not set
```

## 配置要点说明

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `CONFIG_SPIRAM` | y | 启用PSRAM |
| `CONFIG_SPIRAM_MODE_HEX` | y | 16线模式（ESP32-P4唯一选项） |
| `CONFIG_SPIRAM_SPEED_200M` | y | 200MHz（最高性能，需实验性功能） |
| `CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY` | y | BSS段放在PSRAM |
| `CONFIG_SPIRAM_XIP_FROM_PSRAM` | n | **不启用**（避免问题） |

## 验证标准

启动日志应包含：
```
I (xxx) hex_psram: vendor id    : 0x0d (AP)
I (xxx) esp_psram: Found 32MB PSRAM device
I (xxx) esp_psram: Speed: 200MHz
I (xxx) esp_psram: SPI SRAM memory test OK
I (xxx) esp_psram: Adding pool of 32768K of PSRAM memory to heap allocator
```

## ESP32-P4 PSRAM 专用说明

- **只有16线模式**：没有 OCT/QUAD 模式选项
- **时钟选项**：20MHz / 80MHz / 200MHz（200MHz需实验性功能）
- **XIP不推荐**：可能导致启动问题

## 建议

- 保持当前配置，无需启用XIP
- 当前配置已足够支持双目UVC摄像头等应用
