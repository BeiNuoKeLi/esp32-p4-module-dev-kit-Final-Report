/**
 * @file sensors.c
 * @brief 光敏电阻传感器驱动实现
 *
 * 移植说明 (来自 REQUIREMENT.md 十)：
 *   - 51 的 sbit key1=P0^1 → ESP-IDF gpio_get_level(GPIO_NUM_23) 读取 DO
 *   - 51 无 ADC → ESP-IDF adc_oneshot_read() 读取 AO 模拟量
 *   - 51 的 Uart_TxData → ESP_LOGI() 串口输出
 *
 * 传感器特性 (来自 使用说明书)：
 *   - AO: 光照越强 → 电压越高
 *   - DO: 低于阈值→高电平, 超过阈值→低电平 (LM393 比较器输出)
 *   - 工作电压 3.3V~5V
 */

#include "sensors.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "photo_sensor";

/* ADC1 单元句柄 */
static adc_oneshot_unit_handle_t s_adc1_handle;

/* ==================== 内部函数 ==================== */

/**
 * @brief 整数比较函数 (用于 qsort 排序)
 */
static int cmp_int(const void *a, const void *b)
{
    return (*(int *)a - *(int *)b);
}

/**
 * @brief 算术平均滤波 (REQUIREMENT.md 5.5)
 *
 * 流程:
 *   1. 连续采集 12 个 ADC 原始值
 *   2. 排序 (升序)
 *   3. 去掉最大 2 个和最小 2 个
 *   4. 对剩余 8 个取算术平均值
 *
 * 异常检测 (REQUIREMENT.md 5.7):
 *   - 12 次采样全部为 0 或全部为 4095 → 返回 -1
 *
 * @param channel ADC 通道号
 * @return 滤波后的 ADC 值 (0~4095), -1 表示异常
 */
static int adc_filter_sample(adc_channel_t channel)
{
    int samples[ADC_SAMPLE_COUNT];
    int all_zero = 1, all_max = 1;

    for (int i = 0; i < ADC_SAMPLE_COUNT; i++) {
        int raw = 0;
        esp_err_t ret = adc_oneshot_read(s_adc1_handle, channel, &raw);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "ADC 读取失败: %s", esp_err_to_name(ret));
            raw = 0;
        }
        samples[i] = raw;

        /* 异常检测标志 */
        if (raw != 0)    all_zero = 0;
        if (raw != 4095) all_max  = 0;

        /* 采样间隔, 让 ADC 稳定 */
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    /* 异常: 全零或全满 (REQUIREMENT.md 5.7) */
    if (all_zero || all_max) {
        return -1;
    }

    /* 排序 → 去头去尾 → 取平均 */
    qsort(samples, ADC_SAMPLE_COUNT, sizeof(int), cmp_int);

    int sum = 0;
    for (int i = ADC_DISCARD_COUNT; i < ADC_SAMPLE_COUNT - ADC_DISCARD_COUNT; i++) {
        sum += samples[i];
    }
    return sum / (ADC_SAMPLE_COUNT - 2 * ADC_DISCARD_COUNT);
}

/* ==================== 公共函数 ==================== */

esp_err_t photo_sensor_init(void)
{
    /* ---- 1. 初始化 ADC1 单元 ---- */
    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg, &s_adc1_handle));

    /* ---- 2. 配置 ADC1_CH4 (GPIO5): 12 位精度, 11dB 衰减 (满量程 ~3.3V) ---- */
    /*    REQUIREMENT.md 5.4: adc1_config_channel_atten(ADC1_CHANNEL_4, ADC_ATTEN_DB_11) */
    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten    = ADC_ATTEN_DB_12,    /*!< 满量程 ~3.3V (v5.x 新名称, 等价旧版 ADC_ATTEN_DB_11) */
        .bitwidth = ADC_BITWIDTH_12,    /*!< 12 位精度 (0~4095) */
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc1_handle, PHOTO_ADC_CHAN, &chan_cfg));

    /* ---- 3. 配置 DO 引脚 (GPIO23) 为数字输入 ---- */
    /*    使用说明书: DO 为 LM393 比较器输出, TTL 电平 */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << PHOTO_DO_GPIO),
        .mode          = GPIO_MODE_INPUT,
        .pull_up_en    = GPIO_PULLUP_DISABLE,
        .pull_down_en  = GPIO_PULLDOWN_DISABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    ESP_LOGI(TAG, "光敏电阻传感器初始化完成 (AO=GPIO%d/ADC1_CH4, DO=GPIO%d)",
             PHOTO_AO_GPIO, PHOTO_DO_GPIO);
    return ESP_OK;
}

esp_err_t photo_sensor_read(photo_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* ---- 读取 AO 模拟量 (ADC1_CH4) ---- */
    int filtered = adc_filter_sample(PHOTO_ADC_CHAN);
    if (filtered < 0) {
        /* ADC 异常: 全零或全满 (REQUIREMENT.md 5.7: err |= 0x08) */
        data->light_v = 0.0f;
        data->err     = 0x08;
        ESP_LOGE(TAG, "光敏 ADC 采样异常 (全零或全满)");
    } else {
        /* 电压换算 (REQUIREMENT.md 5.4): voltage = adc_reading * 3.3 / 4095.0 */
        data->light_v = (float)filtered * 3.3f / 4095.0f;
        data->err     = 0;
    }

    /* ---- 读取 DO 数字量 (GPIO23) ---- */
    /* 使用说明书: 低于阈值→高电平(1), 超过阈值→低电平(0) */
    data->do_level = gpio_get_level(PHOTO_DO_GPIO);

    return ESP_OK;
}
