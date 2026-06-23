# ESP32-P4 PSRAM 配置指南

## 配置状态

⚠️ **PSRAM 已禁用** — 经全面测试，在 ESP-Hosted 双核 RPC 场景下完全无法稳定运行。

---

## 最终结论：PSRAM + ESP-Hosted = 不可用

经过 2026-06-23 全天测试，覆盖 3 种频率、4 种配置组合、7 项访问模式，结论：

> ESP32-P4 v1.0 + ESP-Hosted（双核 RPC TX/RX） + PSRAM heap = **不可稳定**

即使 `MALLOC_ALWAYSINTERNAL=32768`（32KB 以下全走内部 RAM），ESP-Hosted 的 `rpc_rx_thread`（Core 0）和 `rpc_tx_thread`（Core 1）**同时调用 `calloc`** 时，双核并发访问 PSRAM heap 的 `multi_heap` 元数据 → 堆管理结构被踩 → `MTVAL=0xaaa080e7` 崩溃。此现象 100% 可复现。

---

## 压力测试结论（`psram_test/` 独立工程）

使用专用测试程序，在 **80MHz、BSS 内部 RAM** 条件下，对 AP Memory 32MB HEX PSRAM 进行 7 项访问模式测试：

| # | 测试 | 结果 | 现象 |
|---|------|:--:|------|
| 1 | 顺序读写 1MB | ✅ | 3 次连续通过 |
| 2 | 随机读写 256×4KB | ✅ | 3 次连续通过 |
| 3 | malloc 压力 200 轮 | ✅ | 分配/释放正常 |
| 4 | **跨区 memcpy** | ❌ | PSRAM→内部RAM 拷贝时 FreeRTOS 调度器崩溃 (`MTVAL=0x11111155`) |
| 5 | **非对齐访问** | ❌ | `0x04030201 ≠ 0x00010001`，5 处数据错误 |
| 6 | **并发访问** | ❌ | 双核同时读写 PSRAM → `psram_wr` 死锁，WDT 触发 |

### 主项目压力测试

| 配置 | 冷启 | 稳定性 | 崩溃模式 |
|------|:--:|------|------|
| 200MHz + BSS in PSRAM | ❌ | 几乎无法启动 | H_API init 即崩 |
| 80MHz + BSS in PSRAM | ❌ | 需热身 3-4 次 | FreeRTOS/SPI Flash 随机崩 |
| 80MHz + BSS 内部 RAM | ✅ | 几十秒~几分钟 | RPC protobuf/跨区 memcpy 崩 |
| 80MHz + BSS 内部 RAM + MALLOC_ALWAYSINTERNAL=32K | ❌ | 启动即崩 | 双核并发 calloc → `multi_heap` 损坏 |
| **无 PSRAM** | ✅ | **完全稳定** | 仅 WiFi 重连偶发 WDT（与 PSRAM 无关） |

---

## 为什么之前 200MHz 能长期跑

1. **BSS 在 PSRAM 时**：FreeRTOS/SDIO 缓冲崩溃导致系统频繁重启，反而掩盖了后续缺陷
2. **芯片热态**：连续运行数小时后的温度可能改变 PSRAM 时序裕量
3. **累积退化**：2026-06-23 当日反复 `fullclean + flash` 十余次，热循环可能加速了 PSRAM 信号边界的恶化

---

## 可能原因

| 疑似原因 | 证据 | 可能性 |
|---------|------|:--:|
| **AP Memory PSRAM 兼容性** | Waveshare 使用非乐鑫 ESP-PSRAM；IDF 声明"仅支持乐鑫品牌" | ⭐⭐⭐ |
| **PCB 走线质量** | 第三方板 16 线 80MHz 并行总线，layout 公差不如原厂 | ⭐⭐⭐ |
| **ESP-Hosted 双核 RPC** | RPC TX/RX 分属不同核，并发分配触发硅片缺陷 | ⭐⭐⭐ |
| **v1.0 硅片勘误** | 官方记录 APM-560/750/751 影响早期版本 | ⭐⭐ |

> 由于 PSRAM 完全关闭后系统稳定（8分钟+ WDT 仅 WiFi 重连触发），排除了代码/其他硬件问题。
> 根因无法在软件层面隔离，结论是 v1.0 + ESP-Hosted 场景下 PSRAM 不可用。

---

## 参考链接

- **ESP32-P4 勘误**: https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32p4/
- **ESP-IDF Issue #14979**: https://github.com/espressif/esp-idf/issues/14979
- **ESPHome Issue #16903**: https://github.com/esphome/esphome/issues/16903
- **Bug Report 草稿**: `BUG_REPORT_PSRAM.md`（待提交官方）

---

## PSRAM 恢复与使用

### 恢复 PSRAM

```bash
# sdkconfig:
# # CONFIG_SPIRAM is not set   →   CONFIG_SPIRAM=y
idf.py fullclean build flash monitor
```

### 仅限纯大块缓冲场景

PSRAM 内部自访问（测试 1-3）稳定，但需要满足**全部**条件：
- 大块数据分配在 PSRAM 后**原地使用**，不拷贝回内部 RAM
- 单核单任务访问
- 不对齐访问时用 `memcpy` 对齐
- 不与 ESP-Hosted（双核 RPC）同时使用

典型场景：相机帧缓冲、AI 模型权重。**日常传感器采集 + Wi-Fi 场景请保持关闭**。
