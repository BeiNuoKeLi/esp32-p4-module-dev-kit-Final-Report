/**
 * @file sensors.h
 * @brief 光敏电阻传感器驱动头文件
 *
 * 引脚定义（已根据 ESP32-P4 实际 ADC 通道映射修正）：
 *   - AO → GPIO20 (ADC1_CH4)
 *     依据: soc/esp32p4/include/soc/adc_channel.h:
 *           #define ADC1_CHANNEL_4_GPIO_NUM 20
 *     注意: REQUIREMENT.md 3.1 中 "GPIO5→ADC1_CH4" 有误，GPIO5 在
 *           ESP32-P4 上不是 ADC 引脚，实际 ADC1 从 GPIO16 起步。
 *   - DO → GPIO23 (数字输入, 同时也是 ADC1_CH7, 此处仅作 GPIO 用)
 *
 * 传感器特性来自 使用说明书：
 *   - AO: 光照越强 → 原始值越高
 *   - DO: 低于阈值→高电平(1), 超过阈值→低电平(0)
 *   - 工作电压 3.3V~5V, LM393 比较器
 */

#ifndef SENSORS_H
#define SENSORS_H

#include "esp_err.h"
#include "esp_adc/adc_oneshot.h"

/* ==================== 光敏电阻引脚定义 ==================== */
/* ADC1_CHANNEL_4_GPIO_NUM = 20 (来自 soc/esp32p4/include/soc/adc_channel.h) */
#define PHOTO_AO_GPIO       20              /*!< AO → GPIO20, ADC1_CH4 */
#define PHOTO_DO_GPIO       23              /*!< DO → GPIO23, 数字输入 */
#define PHOTO_ADC_UNIT      ADC_UNIT_1      /*!< 使用 ADC1 单元 */
#define PHOTO_ADC_CHAN      ADC_CHANNEL_4   /*!< GPIO20 对应 ADC1_CH4 */

/* ==================== ADC 滤波参数 (REQUIREMENT.md 5.5) ==================== */
#define ADC_SAMPLE_COUNT    12      /*!< 连续采样次数 */
#define ADC_DISCARD_COUNT   2       /*!< 每端去掉的个数，剩余 8 个取平均 */

/* ==================== 光敏电阻传感器数据结构 ==================== */
typedef struct {
    int     light_raw;  /*!< AO 滤波后 ADC 原始值 (0~4095, 12位) */
    int     do_level;   /*!< DO 电平: 0=超阈值(暗), 1=正常(亮) */
    int     err;        /*!< 错误标志: bit3=光敏ADC异常 (REQUIREMENT.md 5.7) */
} photo_data_t;

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

#endif /* SENSORS_H */
