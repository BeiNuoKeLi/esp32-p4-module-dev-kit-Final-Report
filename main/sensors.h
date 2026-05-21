/**
 * @file sensors.h
 * @brief 光敏电阻传感器驱动头文件
 *
 * 引脚定义来自 REQUIREMENT.md 3.1：
 *   - AO → GPIO5  (ADC1_CH4, 模拟输入)
 *   - DO → GPIO23 (数字输入)
 *
 * 传感器特性来自 使用说明书：
 *   - AO: 光照越强 → 电压越高
 *   - DO: 低于阈值→高电平, 超过阈值→低电平
 *   - 工作电压 3.3V~5V, LM393 比较器
 */

#ifndef SENSORS_H
#define SENSORS_H

#include "esp_err.h"
#include "esp_adc/adc_oneshot.h"

/* ==================== 光敏电阻引脚定义 ==================== */
#define PHOTO_AO_GPIO       5               /*!< AO → GPIO5, ADC1_CH4 (REQUIREMENT.md 3.1) */
#define PHOTO_DO_GPIO       23              /*!< DO → GPIO23, 数字输入 (REQUIREMENT.md 3.1) */
#define PHOTO_ADC_CHAN      ADC_CHANNEL_4   /*!< GPIO5 对应 ADC1 通道 4 */

/* ==================== ADC 滤波参数 (REQUIREMENT.md 5.5) ==================== */
#define ADC_SAMPLE_COUNT    12      /*!< 连续采样次数 */
#define ADC_DISCARD_COUNT   2       /*!< 每端去掉的个数，剩余 8 个取平均 */

/* ==================== 光敏电阻传感器数据结构 ==================== */
typedef struct {
    float   light_v;    /*!< AO 电压值 (V), 2 位小数 */
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
