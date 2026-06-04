# ESP32-P4 开发提示词模板

适用于本项目：`F:\CodeProject\iiot_Experiment_2\code\02_HelloWorld`

---

## 通用模板：开发新传感器/模块

```
ESP32-P4 开发[模块名称]：

1. 先看当前项目状态（必须！）
   - 阅读 memories.md（项目整体状态、已完成模块）
   - 阅读 REQUIREMENT.md 3.1节（引脚分配）、对应模块的技术参数章节
   - 确认现有代码架构：
     - main/sensors.h（数据结构、引脚宏、函数声明）
     - main/sensors.c（驱动实现）
     - main/hello_world_main.c（任务创建）
   - 检查现有代码风格：
     - 命名规范（snake_case 变量/函数，UPPER_CASE 宏）
     - 注释格式（中文，标注数据来源）
     - 错误处理（检查 ESP-IDF API 返回值，标记 err 位）
     - FreeRTOS 任务设置（栈大小 4096，传感器优先级 3，OLED 优先级 2）
     - ADC 使用（共享 ADC1，使用 adc1_shared_init()，复用 adc_filter_sample()）
     - OLED 使用（sensor_shared_t + g_sensor_mutex 共享传感器数据，oled_show_line() 格式化输出）
     - 蜂鸣器使用（buzzer_init()/buzzer_set()，访问 g_sensor_data 的 mq135_do/photo_do/buzzer_on）

2. 总体方案（先给我看，等我确认再动手！）
   - 引脚验证：
     - 目标引脚是否在排针上？（GPIO0-6, 20-27, 32-33, 36, 45-48, 53, 54）
     - ADC 引脚是否正确？（仅 GPIO16-23 = ADC1，查 soc/adc_channel.h）
     - 电平转换要求？（是否需要电阻分压 5V→3.3V）
   - API选择：
     - GPIO：gpio_config/gpio_set_level/gpio_get_level
     - ADC：共享 ADC1，使用 adc1_shared_init() 和 adc_filter_sample()
     - 1-Wire：参考现有 DHT11/DS18B20 实现
   - 软件架构：
     - 新增数据结构？（加在 sensors.h 对应位置）
     - 新增函数？（声明在 sensors.h，实现在 sensors.c）
     - 任务创建？（加在 hello_world_main.c 的 app_main()）

3. 写代码
   - 严格遵循现有风格
   - 写代码时强制检查：
     ✅ 所有 static 函数是否有前置声明？（如果被前面的函数调用）
     ✅ 所有 API 是否与现有代码一致？
     ✅ 变量类型是否匹配？
     ✅ 是否引入了多余的头文件？
     ✅ 所有函数都有中文注释和数据来源标注吗？

4. 更新项目记录
   - 更新 memories.md 的对应章节：
     - 传感器清单
     - 引脚分配表
     - 待办清单
     - 更新记录

5. 写好后给我看，等我确认无误再结束！
```

---

## 专用模板：具体模块示例

### 模板1：蜂鸣器+LED报警

```
ESP32-P4 开发蜂鸣器+LED报警模块：

1. 先看当前项目状态
   - 阅读 memories.md
   - 阅读 REQUIREMENT.md 3.1节（引脚：GPIO25）、4.5节（报警条件：MQ-135 DO=0 或 光敏 DO=0）

2. 总体方案（先给我看，等确认！）
   - 引脚验证：GPIO25 是否在排针上？
   - 电路要求：三极管驱动？（REQUIREMENT.md 3.2）
   - 软件架构：报警函数加在 sensors.c？单独任务？

3. 写代码
   - 检查声明顺序
   - 检查 API 一致性

4. 更新 memories.md

5. 等我确认！
```

---

## 专用模板：修复问题

```
修复 [问题描述]：

1. 先定位问题
   - 阅读编译器错误/日志
   - 定位到具体文件行号

2. 给出修复方案
   - 先讲清楚问题根因
   - 再讲修复方案
   - 等我确认！

3. 修复
   - 最小化改动（只改必要的）
   - 验证修改

4. 给我看修改后的代码片段
```

---

## 专用模板：功能验证

```
验证 [功能名称]：

1. 回顾现有代码
   - 确认功能实现位置

2. 预期行为
   - 串口输出什么？
   - 硬件动作什么？

3. 实际行为（由我提供）
   [在这里粘贴串口日志]

4. 分析差异，给出结论
```

---

## 项目关键规范（写在提示词最前面）

### 引脚规范

| 规则 | 说明 |
|------|------|
| GPIO0-6, 20-27, 32-33, 36, 45-48, 53 | 排针可用 |
| GPIO7-8 | I2C 保留（OLED SSD1306 SDA/SCL） |
| GPIO37-38 | UART 保留 |
| GPIO16-23 | ADC1 专用 |
| GPIO4-5 | **不是** ADC 引脚 |

### 电路规范

| 模块 | 要求 |
|------|------|
| 5V 供电模块 DO | 必须电平转换 5V→3.3V（2KΩ:1KΩ分压） |
| 1-Wire 总线（DHT11/DS18B20） | 外接上拉电阻（5KΩ/4.7KΩ） |
| I2C 总线（OLED SSD1306） | SDA/SCL 各接 4.7KΩ 上拉至 3.3V（或开启芯片内部上拉） |
| MQ-135 加热 | 供电 5V 最佳，3.3V 也可用 |

### 代码规范

| 项 | 规范 |
|----|------|
| 命名 | 变量/函数 snake_case，宏 UPPER_CASE |
| 注释 | 中文，标注数据来源（REQUIREMENT.md / 数据手册） |
| 错误处理 | 检查 API 返回值，设置 err 位（REQUIREMENT.md 5.7） |
| ADC 使用 | 共享 ADC1，调用 adc1_shared_init()，复用 adc_filter_sample() |
| OLED 显示 | sensor_shared_t 共享数据 + g_sensor_mutex 互斥锁，oled_show_line() 格式化 |
| FreeRTOS 任务 | 栈 4096（蜂鸣器 2048），传感器优先级 3，OLED/蜂鸣器优先级 2 |

---

## 提示词撰写检查清单

写提示词前检查：
- [ ] 是否要求先看 memories.md？
- [ ] 是否要求先给总体方案再写代码？
- [ ] 是否列出代码自检清单？
- [ ] 是否要求等确认再结束？

---

> **最后更新**：2026-06-04（UDP 发送模块 + pc_receiver.py 端到端验证通过）
