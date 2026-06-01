/**
 * @file sensors.h
 * @brief 光敏电阻传感器 + DHT11温湿度传感器驱动头文件
 *
 * DHT11 引脚定义（来自 REQUIREMENT.md 3.1）：
 *   - DATA → GPIO2
 *     依据: REQUIREMENT.md 3.1 表格, DHT11 DATA → GPIO 2
 *     说明: 使用开漏输出模式, 需要外接 5KΩ 上拉电阻
 *
 * DHT11 传感器特性（来自 DHT11 数据手册）：
 *   - 温度范围: 0~50°C, 精度 ±2°C, 分辨率 1°C (整数)
 *   - 湿度范围: 20%~90%RH, 精度 ±5%RH, 分辨率 1%RH (整数)
 *   - 采样周期: ≥2秒
 *   - 通信协议: 1-Wire, 40位数据 (湿度整+湿度小+温度整+温度小+校验和)
 *
 * 光敏电阻引脚定义（已根据 ESP32-P4 实际 ADC 通道映射修正）：
 *   - AO → GPIO20 (ADC1_CH4)
 *     依据: soc/esp32p4/include/soc/adc_channel.h:
 *           #define ADC1_CHANNEL_4_GPIO_NUM 20
 *     注意: REQUIREMENT.md 3.1 中 "GPIO5→ADC1_CH4" 有误，GPIO5 在
 *           ESP32-P4 上不是 ADC 引脚，实际 ADC1 从 GPIO16 起步。
 *   - DO → GPIO23 (数字输入, 同时也是 ADC1_CH7, 此处仅作 GPIO 用)
 *
 * 光敏电阻传感器特性来自 使用说明书：
 *   - AO: 光照越强 → 原始值越高
 *   - DO: 低于阈值→高电平(1), 超过阈值→低电平(0)
 *   - 工作电压 3.3V~5V, LM393 比较器
 */

#ifndef SENSORS_H
#define SENSORS_H

#include "esp_err.h"
#include "esp_adc/adc_oneshot.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "stdint.h"

/* ==================== DS18B20 引脚定义 ==================== */
#define DS18B20_DATA_GPIO   1               /*!< DATA → GPIO1 (REQUIREMENT.md 3.1) */

/* ==================== DHT11 引脚定义 ==================== */
#define DHT11_DATA_GPIO     2               /*!< DATA → GPIO2 (REQUIREMENT.md 3.1) */

/* ==================== MQ-135 引脚定义 ==================== */
/* ADC1_CHANNEL_5_GPIO_NUM = 21 (来自 soc/esp32p4/include/soc/adc_channel.h) */
#define MQ135_AO_GPIO       21              /*!< AO → GPIO21, ADC1_CH5 (REQUIREMENT.md 3.1) */
#define MQ135_DO_GPIO       22              /*!< DO → GPIO22, 数字输入, 需电平转换 5V→3.3V (REQUIREMENT.md 3.2) */
#define MQ135_ADC_CHAN      ADC_CHANNEL_5   /*!< GPIO21 对应 ADC1_CH5 */

/* ==================== 光敏电阻引脚定义 ==================== */
/* ADC1_CHANNEL_4_GPIO_NUM = 20 (来自 soc/esp32p4/include/soc/adc_channel.h) */
#define PHOTO_AO_GPIO       20              /*!< AO → GPIO20, ADC1_CH4 */
#define PHOTO_DO_GPIO       23              /*!< DO → GPIO23, 数字输入 */
#define PHOTO_ADC_CHAN      ADC_CHANNEL_4   /*!< GPIO20 对应 ADC1_CH4 */

/* ==================== 蜂鸣器引脚定义 ==================== */
#define BUZZER_GPIO         25              /*!< 蜂鸣器控制 → GPIO25, 数字输出 (REQUIREMENT.md 3.1) */

/* ==================== ADC 滤波参数 (REQUIREMENT.md 5.5) ==================== */
#define ADC_SAMPLE_COUNT    12      /*!< 连续采样次数 */
#define ADC_DISCARD_COUNT   2       /*!< 每端去掉的个数，剩余 8 个取平均 */

/* ==================== DHT11 传感器数据结构 ==================== */
typedef struct {
    int     temp;       /*!< 温度 (°C), 整数 (DHT11 小数恒为0) */
    int     humi;       /*!< 湿度 (%RH), 整数 (DHT11 小数恒为0) */
    int     err;        /*!< 错误标志: bit0=DHT11校验失败/无响应 (REQUIREMENT.md 5.7) */
} dht11_data_t;

/* ==================== DS18B20 传感器数据结构 ==================== */
typedef struct {
    float   temp;       /*!< 温度 (°C), 高精度 0.0625°C (DS18B20 数据手册) */
    int     err;        /*!< 错误标志: bit1=DS18B20无响应 (REQUIREMENT.md 5.7) */
} ds18b20_data_t;

/* ==================== MQ-135 传感器数据结构 ==================== */
typedef struct {
    int     ao_raw;     /*!< AO 滤波后 ADC 原始值 (0~4095, 12位) */
    float   voltage;    /*!< AO 电压 (V), ao_raw * 3.3 / 4095 (REQUIREMENT.md 5.4) */
    int     do_level;   /*!< DO 电平: 0=超阈值(报警), 1=正常 (模块基础参数: TTL低电平有效) */
    int     err;        /*!< 错误标志: bit2=MQ135 ADC异常 (REQUIREMENT.md 5.7) */
} mq135_data_t;

/* ==================== 光敏电阻传感器数据结构 ==================== */
typedef struct {
    int     light_raw;  /*!< AO 滤波后 ADC 原始值 (0~4095, 12位) */
    int     do_level;   /*!< DO 电平: 0=超阈值(暗), 1=正常(亮) */
    int     err;        /*!< 错误标志: bit3=光敏ADC异常 (REQUIREMENT.md 5.7) */
} photo_data_t;

/* ==================== 传感器共享数据结构体 (OLED 显示用) ==================== */
typedef struct {
    /* DHT11 */
    int     dht11_temp;     /*!< DHT11 温度 (°C) */
    int     dht11_humi;     /*!< DHT11 湿度 (%RH) */
    int     dht11_err;      /*!< DHT11 错误标志 */

    /* DS18B20 */
    float   ds18b20_temp;   /*!< DS18B20 温度 (°C) */
    int     ds18b20_err;    /*!< DS18B20 错误标志 */

    /* MQ-135 */
    int     mq135_ao_raw;   /*!< MQ-135 AO 原始值 */
    float   mq135_voltage;  /*!< MQ-135 电压 (V) */
    int     mq135_do;       /*!< MQ-135 DO: 0=超阈值, 1=正常 */
    int     mq135_err;      /*!< MQ-135 错误标志 */

    /* 光敏电阻 */
    int     photo_raw;      /*!< 光敏 AO 原始值 */
    int     photo_do;       /*!< 光敏 DO: 0=超阈值, 1=正常 */
    int     photo_err;      /*!< 光敏错误标志 */

    /* 蜂鸣器 */
    int     buzzer_on;      /*!< 蜂鸣器状态: 0=静音, 1=鸣叫中 */
} sensor_shared_t;

/* 全局共享数据句柄 */
extern sensor_shared_t g_sensor_data;
extern SemaphoreHandle_t g_sensor_mutex;

/* ==================== DHT11 函数声明 ==================== */
/**
 * @brief 初始化 DHT11 温湿度传感器 (GPIO2)
 *
 * - 配置 GPIO2 为开漏输入输出模式
 * - 等待 ≥1秒 让传感器上电稳定 (数据手册要求)
 *
 * @return ESP_OK 成功
 */
esp_err_t dht11_init(void);

/**
 * @brief 读取 DHT11 温湿度传感器数据
 *
 * - 发送起始信号: 拉低 ≥18ms
 * - 等待响应信号
 * - 接收 40位数据 (5字节)
 * - 校验和检查: DATA[4] == DATA[0]+DATA[1]+DATA[2]+DATA[3]
 *
 * @param[out] data DHT11 数据结构体指针
 * @return ESP_OK 成功, ESP_ERR_INVALID_ARG 参数无效, ESP_FAIL 读取/校验失败
 */
esp_err_t dht11_read(dht11_data_t *data);

/* ==================== DS18B20 函数声明 ==================== */
/**
 * @brief 初始化 DS18B20 温度传感器 (GPIO1)
 *
 * - 配置 GPIO1 为开漏输入输出模式
 * - 外接 4.7KΩ 上拉电阻 (REQUIREMENT.md 3.2)
 *
 * @return ESP_OK 成功
 */
esp_err_t ds18b20_init(void);

/**
 * @brief 读取 DS18B20 温度传感器数据
 *
 * 1-Wire 操作流程 (来自参考代码 DS18B20.c):
 *   1. One_Wire_Init(): 主机拉低 500µs → 释放 → 检测从机响应
 *   2. WriteData(0xCC): Skip ROM 命令 (单设备时)
 *   3. WriteData(0x44): 启动温度转换
 *   4. 等待 ≥750ms (12位转换时间)
 *   5. 再次 Init() + 0xCC + 0xBE (读暂存器)
 *   6. 读 2 字节: Temp = (H << 8 | L) / 16.0
 *
 * @param[out] data DS18B20 数据结构体指针
 * @return ESP_OK 成功, ESP_ERR_INVALID_ARG 参数无效, ESP_FAIL 无响应
 */
esp_err_t ds18b20_read(ds18b20_data_t *data);

/* ==================== MQ-135 函数声明 ==================== */
/**
 * @brief 初始化 MQ-135 空气质量传感器 (ADC1_CH5/GPIO21 + GPIO22)
 *
 * - ADC 单元与光敏共享 ADC1，内部调用 adc1_shared_init() 确保只初始化一次
 * - 配置 ADC1_CH5 (GPIO21): 12 位精度, 12dB 衰减
 * - 配置 GPIO22 为数字输入 (DO, TTL 低电平有效)
 * - 注意: MQ-135 模块需预热 ≥3 分钟读数才稳定 (技术手册)
 *
 * @return ESP_OK 成功
 */
esp_err_t mq135_init(void);

/**
 * @brief 读取 MQ-135 空气质量传感器数据
 *
 * - AO: 12 次采样 → 算术平均滤波 → 原始 ADC 值 + 换算电压
 * - DO: 直接读取 GPIO 电平 (0=超阈值/报警, 1=正常)
 *
 * @param[out] data MQ-135 数据结构体指针
 * @return ESP_OK 成功, ESP_ERR_INVALID_ARG 参数无效
 */
esp_err_t mq135_read(mq135_data_t *data);

/* ==================== 光敏电阻函数声明 ==================== */
/**
 * @brief 初始化光敏电阻传感器 (ADC1_CH4 + GPIO23)
 *
 * - 配置 ADC1 单元, 12 位精度, ~3.3V 满量程
 * - 配置 GPIO23 为数字输入
 *
 * @return ESP_OK 成功
 */
esp_err_t photo_sensor_init(void);

/**
 * @brief 读取光敏电阻传感器数据
 *
 * - AO: 12 次采样 → 排序 → 去头去尾各 2 → 剩余 8 取平均 → 换算电压
 * - DO: 直接读取 GPIO 电平
 *
 * @param[out] data 传感器数据结构体指针
 * @return ESP_OK 成功, ESP_ERR_INVALID_ARG 参数无效
 */
esp_err_t photo_sensor_read(photo_data_t *data);

/* ==================== 蜂鸣器函数声明 ==================== */
/**
 * @brief 初始化蜂鸣器 (GPIO25)
 *
 * - 配置 GPIO25 为推挽输出, 初始低电平 (静音)
 * - 下拉使能, 防止上电误触发
 * - 有源蜂鸣器 + S8050 NPN 三极管驱动 (REQUIREMENT.md 3.2)
 *
 * @return ESP_OK 成功
 */
esp_err_t buzzer_init(void);

/**
 * @brief 设置蜂鸣器状态
 *
 * @param on 1=鸣叫, 0=静音
 * @return ESP_OK 成功
 */
esp_err_t buzzer_set(int on);

#endif /* SENSORS_H */
