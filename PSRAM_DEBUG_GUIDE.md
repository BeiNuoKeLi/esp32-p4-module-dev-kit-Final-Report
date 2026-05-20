# ESP32-P4 PSRAM 调试记录与恢复指南

## 项目信息

- **芯片**: ESP32-P4 (eco2, silicon v1.0)
- **ESP-IDF 版本**: v5.5.1
- **工具链版本**: idf5.5_py3.11
- **物理 PSRAM**: 32MB (板上焊接)
- **串口**: COM9, 115200 baud
- **项目路径**: `f:/CodeProject/iiot_Experiment_2/code/02_HelloWorld`

---

## 问题演进过程

### 阶段 1：PSRAM 全开 → LP_WDT Reset 死循环

**现象**:
```
ESP-ROM:esp32p4-eco2-20240710
rst:0x7 (CHIP_LP_WDT_RESET),boot:0x30f (SPI_FAST_FLASH_BOOT)
```
程序在进入 `app_main()` 前就被 Low-Power Watchdog 触发复位，无限循环。

**原因**: ESP32-P4 在启用 PSRAM XIP 功能时，与 ROM 启动流程产生冲突（已知 GitHub Issue #14979）。

**配置状态** (`sdkconfig`):
```ini
CONFIG_SPIRAM=y
CONFIG_SPIRAM_XIP_FROM_PSRAM=y        # ← 问题源
CONFIG_SPIRAM_FETCH_INSTRUCTIONS=y
CONFIG_SPIRAM_RODATA=y
```

### 阶段 2：关闭 XIP，保留 PSRAM → StoreAccessFault

**改动**: 关闭 `SPIRAM_XIP_FROM_PSRAM`、`SPIRAM_FETCH_INSTRUCTIONS`、`SPIRAM_RODATA`

**现象**:
```
Guru Meditation Error: StoreAccessFault
```
PSRAM 初始化阶段 `psram_init()` 内部崩溃。

**结论**: 问题不限于 XIP，PSRAM 底层初始化（时钟/时序）也有兼容性问题。

### 阶段 3：完全禁用 PSRAM → 正常运行 ✅

**改动**: `CONFIG_SPIRAM=n`

**现象**: 程序正常运行，串口输出正常：
```
Hello world!
This is esp32p4 chip with 2 CPU core(s), silicon revision v1.0, 16MB external flash
Minimum free heap size: 468476 bytes
```

**内存分布** (无 PSRAM):
```
I (208) heap_init: At 4FF118B0 len 00029710 (165 KiB): RAM
I (218) heap_init: At 4FF3AFC0 len 00004BF0 (18 KiB): RAM
I (223) heap_init: At 4FF40000 len 00040000 (256 KiB): RAM
I (228) heap_init: At 50108080 len 00007F80 (31 KiB): RTCRAM
I (233) heap_init: At 30100044 len 00001FBC (7 KiB): TCM
```

---

## 当前状态

| 文件 | 状态 |
|------|------|
| `main/hello_world_main.c` | ESP-IDF 官方模板（无任何调试代码） |
| `sdkconfig.defaults` | 仍含 `CONFIG_SPIRAM=y`（模板，未实装） |
| `sdkconfig` (实际生效) | `# CONFIG_SPIRAM is not set`（PSRAM 已禁用） |
| 程序行为 | HelloWorld 正常运行，每 10 秒自动重启（预期行为） |

---

## PSRAM 恢复方案（渐进式排查）

> **核心原则**: 每次只改一个变量，改完执行 `idf.py fullclean && idf.py build && idf.py flash monitor` 验证。

### 步骤 1：最小化安全配置（首选尝试）

目标是让 PSRAM 以最保守的参数初始化成功，仅作为 `malloc` 堆使用。

修改 `sdkconfig.defaults`（或直接在 `menuconfig` 中设置）：

```ini
# --- 启用 PSRAM ---
CONFIG_SPIRAM=y

# --- 保守时钟：降到 40MHz ---
CONFIG_SPIRAM_SPEED_40M=y
# CONFIG_SPIRAM_SPEED_80M is not set

# --- 开启 2T 模式（稳定性关键）---
CONFIG_SPIRAM_2T_MODE=y

# --- XIP 相关：绝不启用 ---
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set
# CONFIG_SPIRAM_FETCH_INSTRUCTIONS is not set
# CONFIG_SPIRAM_RODATA is not set

# --- 高级特性：先关闭 ---
# CONFIG_SPIRAM_MEMTEST is not set
# CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY is not set
# CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY is not set
```

**验证标准**: 串口出现 `Hello world!` 且 `heap_init` 中包含 PSRAM 段。

### 步骤 2：提升到 80MHz

前提：步骤 1 成功。

```ini
CONFIG_SPIRAM_SPEED_80M=y
# CONFIG_SPIRAM_SPEED_40M is not set
```

### 步骤 3：逐步启用高级功能

按以下顺序逐一启用，每改一项测试一次：

| 优先级 | 配置项 | 作用 | 建议 |
|--------|--------|------|------|
| 低 | `CONFIG_SPIRAM_MEMTEST=y` | 上电测试 PSRAM | 建议开启 |
| 中 | `CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y` | BSS 段放 PSRAM | 谨慎 |
| 高 | `CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y` | 任务栈放 PSRAM | 高风险 |

### 永不启用（已知 Bug）

```ini
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set  # ← 必崩
# CONFIG_SPIRAM_FETCH_INSTRUCTIONS is not set  # ← 必崩
# CONFIG_SPIRAM_RODATA is not set              # ← 必崩
```

---

## 备用方案：升级/补丁

如果步骤 1 仍然失败：

1. **升级 ESP-IDF** → v5.5.2 或 v6.0+（官方可能已修复 PSRAM 初始化问题）
2. **手动修复源码** → 修改 `components/esp_psram/esp_psram_impl_quad.c`：
   - 增大 CS 保持时间
   - 调整缓存初始化延迟
3. **联系 Espressif 技术支持** → 提交 GitHub Issue，附上完整日志

---

## 常用命令

```powershell
# 设置 IDF 环境
$env:IDF_PATH = 'F:\BeiNuoKeLi\esp\v5.5.1\esp-idf'

# 完全清理
& 'F:\BeiNuoKeLi\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' 'F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\tools\idf.py' fullclean

# 编译 + 烧录 + 监控
& 'F:\BeiNuoKeLi\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' 'F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\tools\idf.py' build flash monitor -p COM9

# 手动监控（不重新烧录）
& 'F:\BeiNuoKeLi\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe' 'F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\tools\idf_monitor.py' -p COM9 -b 115200 --toolchain-prefix riscv32-esp-elf- --make '''F:\BeiNuoKeLi\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'' ''F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\tools\idf.py''' --target esp32p4 'f:\CodeProject\iiot_Experiment_2\code\02_HelloWorld\build\HelloWorld.elf'
```

---

## 快速判定表

| 串口输出关键词 | 含义 | 下一步 |
|---------------|------|--------|
| `Hello world!` | PSRAM 正常 | 继续步骤 2/3 |
| `CHIP_LP_WDT_RESET` | PSRAM 初始化卡死 | 检查时钟/2T模式 |
| `StoreAccessFault` | PSRAM 内存访问异常 | 检查硬件或降频 |
| `Corrupted` | PSRAM 数据错误 | 开启 MEMTEST 检查 |

---

*最后更新: 2026-05-20 13:14*
