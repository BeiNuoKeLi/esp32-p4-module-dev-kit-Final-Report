# ESP32-P4 PSRAM 调试记录与恢复指南

## 项目信息

- **芯片**: ESP32-P4 (eco2, silicon v1.0)
- **ESP-IDF 版本**: v5.5.1
- **工具链版本**: idf5.5_py3.11
- **物理 PSRAM**: 32MB (板上焊接)
- **串口**: COM9, 115200 baud
- **项目路径**: `f:/CodeProject/iiot_Experiment_2/code/02_HelloWorld`

---

## ⚠️ 重要修正：ESP32-P4 PSRAM 真实配置选项

根据 ESP-IDF v5.5.1 源码分析，以下是 **ESP32-P4 专用 PSRAM 配置**：

### 线宽模式（Line Mode）
| 配置项 | 说明 | ESP32-P4 默认 |
|--------|------|---------------|
| `CONFIG_SPIRAM_MODE_HEX` | **16线模式 PSRAM** | ✅ 选中 |

**注意**：ESP32-P4 **没有** `SPIRAM_MODE_OCT` 或 `SPIRAM_MODE_QUAD`，只有 **HEX (16线)** 模式！

### 时钟频率（Clock Speed）
| 配置项 | 说明 | 依赖 |
|--------|------|------|
| `CONFIG_SPIRAM_SPEED_20M` | 20MHz（最稳定） | - |
| `CONFIG_SPIRAM_SPEED_80M` | 80MHz | - |
| `CONFIG_SPIRAM_SPEED_200M` | 200MHz | 需要 `IDF_EXPERIMENTAL_FEATURES` |

**注意**：没有 40MHz 选项！

### 电压控制（LDO Regulator）
| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CONFIG_ESP_LDO_RESERVE_PSRAM` | `y` | 预留 LDO 通道为 PSRAM 供电 |
| `CONFIG_ESP_LDO_VOLTAGE_PSRAM_DOMAIN` | `1900` | PSRAM 电压 1.9V |

**注意**：ESP32-P4 使用内部 LDO 为 PSRAM 供电，默认 1.9V。

### 不存在的配置项（已移除）
以下配置在 ESP32-P4 上**不存在**，请不要使用：
- ❌ `CONFIG_SPIRAM_2T_MODE`
- ❌ `CONFIG_SPIRAM_MODE_OCT`
- ❌ `CONFIG_SPIRAM_MODE_QUAD`
- ❌ `CONFIG_SPIRAM_SPEED_40M`
- ❌ `CONFIG_SPIRAM_CLK_IO_27`
- ❌ `CONFIG_SPIRAM_CS_IO_26`
- ❌ `CONFIG_SPIRAM_HW_CS_EN`

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

## 📋 ESP32-P4 PSRAM 完整配置宏定义

### 一、启用与初始化

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_SPIRAM` | `n` | **主开关**：启用外部 PSRAM 支持 |
| `CONFIG_SPIRAM_BOOT_HW_INIT` | `y` | 启动时初始化 PSRAM 硬件 |
| `CONFIG_SPIRAM_BOOT_INIT` | `y` | 完整初始化 PSRAM（硬件+内存） |
| `CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION` | `y` | 预配置内存保护 |
| `CONFIG_SPIRAM_IGNORE_NOTFOUND` | `n` | PSRAM 未找到时不报错 |
| `CONFIG_SPIRAM_MEMTEST` | `y` | 初始化时运行内存测试 |

### 二、线宽模式

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_SPIRAM_MODE_HEX` | 选中 | **16线模式 PSRAM**（ESP32-P4 唯一选项） |
| `CONFIG_SPIRAM_USE_8LINE_MODE` | `n` | 启用 8线模式 AP HEX PSRAM |

### 三、时钟频率

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_SPIRAM_SPEED` | `20` | PSRAM 时钟速度（MHz） |
| `CONFIG_SPIRAM_SPEED_20M` | 选中 | 20MHz |
| `CONFIG_SPIRAM_SPEED_80M` | - | 80MHz |
| `CONFIG_SPIRAM_SPEED_200M` | - | 200MHz（需 `IDF_EXPERIMENTAL_FEATURES`） |

### 四、电压控制（LDO）

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_ESP_LDO_RESERVE_PSRAM` | `y` | 预留 LDO 通道为 PSRAM 供电 |
| `CONFIG_ESP_LDO_CHAN_PSRAM_DOMAIN` | `2` | PSRAM LDO 通道（固定为 2） |
| `CONFIG_ESP_LDO_VOLTAGE_PSRAM_DOMAIN` | `1900` | PSRAM 电压（1.9V） |

### 五、高级功能

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_SPIRAM_XIP_FROM_PSRAM` | `n` | 从 PSRAM 就地执行（高风险） |
| `CONFIG_SPIRAM_ECC_ENABLE` | `n` | 启用 ECC（占用 1/8 容量） |
| `CONFIG_SPIRAM_FETCH_INSTRUCTIONS` | - | 指令段移至 PSRAM |
| `CONFIG_SPIRAM_RODATA` | - | 只读数据段移至 PSRAM |

### 六、内存分配

| 宏定义名称 | 默认值 | 说明 |
|-----------|--------|------|
| `CONFIG_SPIRAM_USE_MALLOC` | 选中 | malloc 可分配 PSRAM |
| `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` | `16384` | 小于此值优先内部内存 |
| `CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL` | `32768` | 为 DMA 保留内部内存 |

---

## PSRAM 恢复方案（渐进式排查）

> **核心原则**: 每次只改一个变量，改完执行 `idf.py fullclean && idf.py build && idf.py flash monitor` 验证。

### 步骤 1：最小化安全配置（首选尝试）

目标是让 PSRAM 以最保守的参数初始化成功，仅作为 `malloc` 堆使用。

修改 `sdkconfig.defaults`（或直接在 `menuconfig` 中设置）：

```ini
# ==================== PSRAM 最小安全配置 ====================
CONFIG_SPIRAM=y

# --- 线宽模式（ESP32-P4 专用）---
CONFIG_SPIRAM_MODE_HEX=y

# --- 保守时钟：20MHz（最稳定）---
CONFIG_SPIRAM_SPEED_20M=y
# CONFIG_SPIRAM_SPEED_80M is not set
# CONFIG_SPIRAM_SPEED_200M is not set

# --- 初始化配置 ---
CONFIG_SPIRAM_BOOT_HW_INIT=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION=y

# --- XIP 相关：禁用！ ---
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set
# CONFIG_SPIRAM_FETCH_INSTRUCTIONS is not set
# CONFIG_SPIRAM_RODATA is not set

# --- 内存测试：先开启验证硬件 ---
CONFIG_SPIRAM_MEMTEST=y

# --- 内存分配方式 ---
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768

# --- 高级功能：先关闭 ---
# CONFIG_SPIRAM_ECC_ENABLE is not set
# CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY is not set
# CONFIG_SPIRAM_ALLOW_NOINIT_SEG_EXTERNAL_MEMORY is not set
```

**验证标准**: 串口出现 `Hello world!` 且 `heap_init` 中包含 PSRAM 段。

### 步骤 2：提升到 80MHz

前提：步骤 1 成功。

```ini
# CONFIG_SPIRAM_SPEED_20M is not set
CONFIG_SPIRAM_SPEED_80M=y
```

### 步骤 3：尝试 200MHz（实验性功能）

前提：步骤 2 成功，且愿意使用实验性功能。

```ini
# 先启用实验性功能
CONFIG_IDF_EXPERIMENTAL_FEATURES=y

# 然后选择 200MHz
# CONFIG_SPIRAM_SPEED_80M is not set
CONFIG_SPIRAM_SPEED_200M=y
```

### 步骤 4：逐步启用高级功能

按以下顺序逐一启用，每改一项测试一次：

| 优先级 | 配置项 | 作用 | 建议 |
|--------|--------|------|------|
| 低 | `CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y` | BSS 段放 PSRAM | 谨慎 |
| 中 | `CONFIG_SPIRAM_ECC_ENABLE=y` | 启用 ECC | 占用 1/8 容量 |
| 高 | `CONFIG_SPIRAM_XIP_FROM_PSRAM=y` | 从 PSRAM 执行 | ⚠️ 高风险 |

### 电压配置（如果使用外部电源）

如果 PSRAM 由外部电源供电，可以释放 LDO 通道：

```ini
# CONFIG_ESP_LDO_RESERVE_PSRAM is not set
```

---

## 🎯 重要发现：微雪官方配置

从微雪官方提供的 `esp32-p4-platform` 中找到了他们的 `02_HelloWorld` 示例的 **官方配置**：

```ini
# 来自 f:\CodeProject\iiot_Experiment_2\esp32-p4-platform\examples\esp-idf\02_HelloWorld\sdkconfig.defaults
CONFIG_SPIRAM=y
CONFIG_SPIRAM_SPEED_200M=y
CONFIG_SPIRAM_XIP_FROM_PSRAM=y
```

微雪官方配置**非常激进**：
- ✅ 启用PSRAM
- ✅ 200MHz 时钟速度
- ✅ 启用XIP (Execute In Place)

这说明硬件本身是支持这些功能的！问题可能出在ESP-IDF版本或其他配置上。

---

## 📋 您之前能用的完整配置（来自 WIFI_UVC 项目）

```ini
# 来自 F:\CodeProject\esp32\WIFI_UVC\sdkconfig
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_HEX=y
# CONFIG_SPIRAM_SPEED_80M is not set
CONFIG_SPIRAM_SPEED_20M=y
CONFIG_SPIRAM_SPEED=20
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set  # 关键！禁用XIP
# CONFIG_SPIRAM_ECC_ENABLE is not set
CONFIG_SPIRAM_BOOT_HW_INIT=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION=y
# CONFIG_SPIRAM_IGNORE_NOTFOUND is not set
# CONFIG_SPIRAM_USE_CAPS_ALLOC is not set
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MEMTEST=y  # 启用内存测试
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
# CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP is not set
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
# CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY is not set
# CONFIG_SPIRAM_ALLOW_NOINIT_SEG_EXTERNAL_MEMORY is not set
CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y  # 可以把栈放到PSRAM
```

---

## 详细DEBUG步骤规划

### 阶段零：直接使用您之前能用的配置（最推荐！）

既然您有之前正常工作的配置，我们直接使用那个配置！

#### 0.1 从您的旧项目中提取配置
在当前项目的 `sdkconfig.defaults` 中，添加以下PSRAM相关配置：

```ini
# ==================== PSRAM 配置（来自之前能用的 WIFI_UVC 项目）====================
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_HEX=y
CONFIG_SPIRAM_SPEED_20M=y
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MEMTEST=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768
```

或者更简单的方法，直接复制您旧项目的sdkconfig：

```powershell
# 备份当前配置
Copy-Item sdkconfig sdkconfig.backup.current

# 复制您之前能用的配置
Copy-Item F:\CodeProject\esp32\WIFI_UVC\sdkconfig .
```

#### 0.2 构建并测试
```powershell
idf.py fullclean
idf.py build flash monitor -p COM9
```

这应该能直接工作！如果不行，再继续下面的步骤。

---

### 阶段零备选：直接使用微雪官方配置

如果上面的配置不行，再试试微雪官方的：

```powershell
# 复制微雪官方的配置
Copy-Item f:\CodeProject\iiot_Experiment_2\esp32-p4-platform\examples\esp-idf\02_HelloWorld\sdkconfig.defaults .
```

注意微雪官方使用：
- `CONFIG_SPIRAM_SPEED_200M=y` (200MHz高速)
- `CONFIG_SPIRAM_XIP_FROM_PSRAM=y` (启用XIP)

---

### 阶段一：硬件验证（优先）

#### 1.1 电源检查
```
1. 测量PSRAM VDD引脚电压：应该在1.9V±5%范围内
2. 检查LDO输出是否稳定（CONFIG_ESP_LDO_VOLTAGE_PSRAM_1900_MV设为1.9V）
3. 确认电源纹波小于50mV
4. 检查电源去耦电容是否焊接正确
```

#### 1.2 LDO 配置验证
确保 LDO 配置正确（如果使用内部 LDO）：
```ini
CONFIG_ESP_LDO_RESERVE_PSRAM=y
CONFIG_ESP_LDO_VOLTAGE_PSRAM_1900_MV=y
```

---

### 阶段二：软件最小化配置（渐进式）

#### 2.1 创建 sdkconfig.defaults 最小配置
在项目根目录创建或修改 `sdkconfig.defaults`：

```ini
# 基础系统配置
CONFIG_ESP32P4_DEFAULT_CPU_FREQ_MHZ_240=y
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ=240
CONFIG_BOOTLOADER_LOG_LEVEL_DEBUG=y
CONFIG_LOG_DEFAULT_LEVEL_DEBUG=y

# ==================== PSRAM 最小安全配置 ====================
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_HEX=y
CONFIG_SPIRAM_SPEED_20M=y
CONFIG_SPIRAM_BOOT_HW_INIT=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION=y
CONFIG_SPIRAM_MEMTEST=y
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=32768

# 禁用高风险功能
# CONFIG_SPIRAM_XIP_FROM_PSRAM is not set
# CONFIG_SPIRAM_FETCH_INSTRUCTIONS is not set
# CONFIG_SPIRAM_RODATA is not set
# CONFIG_SPIRAM_ECC_ENABLE is not set
# CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY is not set
# CONFIG_SPIRAM_ALLOW_NOINIT_SEG_EXTERNAL_MEMORY is not set
```

#### 2.2 备份和清理
```powershell
# 备份当前sdkconfig
Copy-Item sdkconfig sdkconfig.backup.psram.disabled

# 完全清理构建
idf.py fullclean
```

#### 2.3 修改 menuconfig 并编译
```powershell
# 打开配置界面
idf.py menuconfig
```

在 menuconfig 中按以下顺序配置：
```
→ Component config
  → ESP PSRAM
    [*] Support for external PSRAM
    → PSRAM config
      (16-Line-Mode PSRAM) Line Mode of PSRAM chip in use
      (20MHz) Set PSRAM clock speed
      [ ] Enable executable in place from (XiP) from PSRAM
      [*] Initialize PSRAM during startup
      [*] Run memory test on SPI RAM initialization
      → SPI RAM access method
        (Make RAM allocatable using malloc() as well)
```

保存退出后：
```powershell
# 构建并烧录
idf.py build flash monitor -p COM9
```

---

### 阶段三：故障排查流程

#### 3.1 如果仍然 LP_WDT 复位
```
可能原因：
1. PSRAM 根本没有响应
2. 时钟问题
3. 电源不稳定

排查步骤：
1. 检查 LDO 配置（CONFIG_ESP_LDO_RESERVE_PSRAM=y）
2. 检查启动日志中关于PSRAM的初始化信息
3. 用示波器查看 CS 和 CLK 引脚是否有信号
4. 确认没有使用不存在的配置项（如 SPIRAM_2T_MODE）
```

#### 3.2 如果发生 StoreAccessFault
```
可能原因：
1. PSRAM 型号不匹配
2. 线宽模式配置错误
3. 硬件连接问题

排查步骤：
1. 确认使用 CONFIG_SPIRAM_MODE_HEX（不是 OCT/QUAD）
2. 检查 SPI 引脚配置
3. 查看 esp-idf/components/esp_psram/ 中的源码
```

#### 3.3 如果检测到 PSRAM 但读取数据错误
```
可能原因：
1. 时钟过快
2. 电源不稳定

排查步骤：
1. 确保 CONFIG_SPIRAM_MEMTEST=y 开启内存测试
2. 确认使用 20MHz 而不是 80MHz/200MHz
3. 检查电源纹波
```

---

### 阶段四：逐步增加功能

#### 4.1 第一步：仅提升到 80MHz
```ini
# CONFIG_SPIRAM_SPEED_20M is not set
CONFIG_SPIRAM_SPEED_80M=y
```

#### 4.2 第二步：尝试 200MHz（需要实验性功能）
```ini
CONFIG_IDF_EXPERIMENTAL_FEATURES=y
# CONFIG_SPIRAM_SPEED_80M is not set
CONFIG_SPIRAM_SPEED_200M=y
```

#### 4.3 第三步：启用 .bss 段到 PSRAM
```ini
CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y
```

#### 4.4 第四步：尝试 ECC（可选）
```ini
CONFIG_SPIRAM_ECC_ENABLE=y
```
⚠️ 注意：启用 ECC 会占用 1/8 的 PSRAM 容量。

#### 4.5 第五步：XIP（高风险，慎选）
```ini
CONFIG_SPIRAM_XIP_FROM_PSRAM=y
```
⚠️ 注意：这可能导致启动问题，仅在所有其他功能都稳定后尝试。

---

### 阶段五：如果所有尝试都失败

#### 5.1 检查 ESP-IDF 版本
```
当前使用 v5.5.1
可以尝试：
1. 升级到最新版本
2. 降级到 v5.4.x 或 v5.3.x
3. 查看是否有相关 issue 修复
```

#### 5.2 检查芯片版本
```
ESP32-P4 有不同的芯片版本：
- Rev 0: 早期版本
- Rev 1: 当前项目使用

检查方法：
在启动日志中查看芯片版本信息，或使用 esptool：
esptool.py -p COM9 chip_id
```

#### 5.3 联系技术支持
```
收集以下信息：
1. 完整的启动日志
2. sdkconfig 配置
3. 硬件连接情况
4. 已尝试的排查步骤

提交到：
- Espressif GitHub Issues
- 官方技术支持论坛
```

---

## 快速判定表

| 串口输出关键词 | 含义 | 下一步 |
|---------------|------|--------|
| `Hello world!` | PSRAM 正常 | 继续步骤 2/3 |
| `CHIP_LP_WDT_RESET` | PSRAM 初始化卡死 | 检查时钟/LDO配置 |
| `StoreAccessFault` | PSRAM 内存访问异常 | 检查硬件或降频 |
| `Corrupted` | PSRAM 数据错误 | 开启 MEMTEST 检查 |
| `SPI RAM enabled` | PSRAM 检测成功 | 继续验证稳定性 |
| `Could not find SPI RAM chip` | PSRAM 未响应 | 检查硬件连接 |

---

## 备用方案：升级/补丁

如果步骤 1 仍然失败：

1. **升级 ESP-IDF** → v5.5.2 或 v6.0+（官方可能已修复 PSRAM 初始化问题）
2. **联系 Espressif 技术支持** → 提交 GitHub Issue，附上完整日志

---

## 常用命令速查

```powershell
# 环境设置
$env:IDF_PATH = "F:\BeiNuoKeLi\esp\v5.5.1\esp-idf"
& "F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\export.ps1"

# 配置
idf.py menuconfig

# 构建流程
idf.py fullclean
idf.py build
idf.py flash -p COM9
idf.py monitor -p COM9

# 快速组合命令
idf.py build flash monitor -p COM9

# 仅监控（不重新烧录）
idf.py monitor -p COM9

# 擦除 flash（慎用）
idf.py erase-flash -p COM9

# 查看芯片信息
esptool.py -p COM9 chip_id
esptool.py -p COM9 flash_id
```

---

## 关键文件位置

- ESP-IDF 根目录: [F:\BeiNuoKeLi\esp\v5.5.1\esp-idf](file:///F:\BeiNuoKeLi\esp\v5.5.1\esp-idf)
- PSRAM 驱动源码: [F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_psram](file:///F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_psram)
- ESP32-P4 PSRAM 配置: [F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_psram\esp32p4\Kconfig.spiram](file:///F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_psram\esp32p4\Kconfig.spiram)
- LDO 配置: [F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_hw_support\port\esp32p4\Kconfig.ldo](file:///F:\BeiNuoKeLi\esp\v5.5.1\esp-idf\components\esp_hw_support\port\esp32p4\Kconfig.ldo)

---

*最后更新: 2026-05-26*
