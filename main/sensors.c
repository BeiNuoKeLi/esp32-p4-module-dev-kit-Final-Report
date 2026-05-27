/**
 * @file sensors.c
 * @brief 光敏电阻传感器 + DHT11温湿度传感器驱动实现
 *
 * DHT11 引脚说明 (REQUIREMENT.md 3.1):
 *   - DATA → GPIO2
 *
 * DHT11 移植说明 (来自 REQUIREMENT.md 十 + 51参考代码):
 *   - 51 sbit DHT11_DQ = P2^0 → ESP-IDF gpio_set_direction(GPIO_NUM_2, GPIO_MODE_INPUT_OUTPUT_OD)
 *   - 51 DHT11_DQ = 0/1 → ESP-IDF gpio_set_level(GPIO_NUM_2, 0/1)
 *   - 51 if(DHT11_DQ) → ESP-IDF gpio_get_level(GPIO_NUM_2)
 *   - 51 Delay1ms(20) → ESP-IDF vTaskDelay(pdMS_TO_TICKS(20))
 *   - 51 DHT11_Delay40us() → ESP-IDF portDISABLE_INTERRUPTS() + esp_rom_delay_us(40) + portENABLE_INTERRUPTS()
 *     (关键时序需关中断防止FreeRTOS任务调度干扰，REQUIREMENT.md 5.6)
 *
 * DHT11 协议 (来自 数据手册 + 51参考代码):
 *   1. 起始: 主机拉低 ≥18ms → 释放 → 等待40us → 检测响应
 *   2. 响应: DHT11拉低80us → 拉高80us
 *   3. 接收: 40bit (5字节): 湿度整 + 湿度小 + 温度整 + 温度小 + 校验和
 *   4. 数据位0: 50us低 + ~28us高; 数据位1: 50us低 + ~70us高
 *   5. 校验: DATA[4] == DATA[0]+DATA[1]+DATA[2]+DATA[3]
 *
 * 光敏电阻引脚修正说明:
 *   - REQUIREMENT.md 中 "GPIO5 → ADC1_CH4" 是错误的。
 *     根据 IDF 源码 soc/esp32p4/include/soc/adc_channel.h:
 *       ADC1_CHANNEL_4_GPIO_NUM = 20  (GPIO20)
 *       ADC1 仅支持 GPIO16~GPIO23
 *     GPIO5 在 ESP32-P4 上不是 ADC 引脚，已将 AO 改到 GPIO20。
 *
 * 光敏电阻移植说明 (来自 REQUIREMENT.md 十):
 *   - 51 的 sbit key1=P0^1 → ESP-IDF gpio_get_level(GPIO_NUM_23) 读取 DO
 *   - 51 无 ADC → ESP-IDF adc_oneshot_read() 读取 AO 模拟量
 *   - 输出原始 ADC 值，不换算电压
 *
 * 光敏电阻传感器特性 (来自 使用说明书):
 *   - AO: 光照越强 → 原始值越高 (0~4095)
 *   - DO: 低于阈值→高电平, 超过阈值→低电平 (LM393 比较器输出)
 *   - 工作电压 3.3V~5V
 */

#include "sensors.h"
#include "esp_log.h"
#include "esp_adc/adc_oneshot.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_rom_sys.h"  /* esp_rom_delay_us */
#include <stdlib.h>   /* qsort */

static const char *TAG_DHT11 = "dht11";
static const char *TAG_PHOTO = "photo_sensor";

/* ADC1 单元句柄 */
static adc_oneshot_unit_handle_t s_adc1_handle;

/* ==================== DHT11 内部函数 ==================== */

/**
 * @brief DHT11 复位并检测响应 (来自51参考代码 DHT11_ReadData)
 *
 * @return 0 成功, -1 无响应
 */
static int dht11_reset(void)
{
    /* 1. 主机拉低 ≥18ms (参考代码用20ms) */
    gpio_set_level(DHT11_DATA_GPIO, 0);
    vTaskDelay(pdMS_TO_TICKS(20));

    /* 2. 释放总线, 拉高 */
    gpio_set_level(DHT11_DATA_GPIO, 1);

    /* 3. 等待40us (关键时序，关中断) */
    portDISABLE_INTERRUPTS();
    esp_rom_delay_us(40);
    portENABLE_INTERRUPTS();

    /* 4. 检测响应: 应该为低电平 */
    if (gpio_get_level(DHT11_DATA_GPIO) != 0) {
        ESP_LOGW(TAG_DHT11, "无响应信号");
        return -1;
    }

    /* 5. 等待响应低电平结束 (80us) */
    portDISABLE_INTERRUPTS();
    while (gpio_get_level(DHT11_DATA_GPIO) == 0);
    /* 6. 等待响应高电平结束 (80us) */
    while (gpio_get_level(DHT11_DATA_GPIO) == 1);
    portENABLE_INTERRUPTS();

    return 0;
}

/**
 * @brief DHT11 读取一个字节 (来自51参考代码 DHT11_ReadData)
 *
 * @return 读取到的字节
 */
static uint8_t dht11_read_byte(void)
{
    uint8_t byte = 0;

    for (int i = 0; i < 8; i++) {
        /* 等待数据前置信号50us低电平结束 */
        portDISABLE_INTERRUPTS();
        while (gpio_get_level(DHT11_DATA_GPIO) == 0);

        /* 等待40us左右判断是1还是0 */
        esp_rom_delay_us(40);

        /* 如果是高电平 → 数据1; 低电平 → 数据0 */
        if (gpio_get_level(DHT11_DATA_GPIO) == 1) {
            byte |= (0x80 >> i);
            /* 等待高电平结束 */
            while (gpio_get_level(DHT11_DATA_GPIO) == 1);
        }
        portENABLE_INTERRUPTS();
    }

    return byte;
}

/* ==================== DHT11 公共函数 ==================== */

esp_err_t dht11_init(void)
{
    /* 配置 DATA 引脚为开漏输入输出模式 (1-Wire总线要求) */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << DHT11_DATA_GPIO),
        .mode          = GPIO_MODE_INPUT_OUTPUT_OD,  /* 开漏模式 */
        .pull_up_en    = GPIO_PULLUP_DISABLE,        /* 外部接5K上拉电阻 */
        .pull_down_en  = GPIO_PULLDOWN_DISABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    /* 初始拉高总线 */
    gpio_set_level(DHT11_DATA_GPIO, 1);

    /* 等待 ≥1秒 让传感器上电稳定 (数据手册要求) */
    ESP_LOGI(TAG_DHT11, "等待 DHT11 上电稳定 (1秒)...");
    vTaskDelay(pdMS_TO_TICKS(1000));

    ESP_LOGI(TAG_DHT11, "DHT11 初始化完成 (DATA=GPIO%d)", DHT11_DATA_GPIO);
    return ESP_OK;
}

esp_err_t dht11_read(dht11_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t dht11_data[5];

    /* 1. 复位并检测响应 */
    if (dht11_reset() != 0) {
        data->temp = -1;
        data->humi = -1;
        data->err  = 0x01;
        return ESP_FAIL;
    }

    /* 2. 读取 5 字节数据 */
    for (int j = 0; j < 5; j++) {
        dht11_data[j] = dht11_read_byte();
    }

    /* 3. 拉高总线 */
    gpio_set_level(DHT11_DATA_GPIO, 1);

    /* 4. 校验和检查 */
    uint8_t checksum = dht11_data[0] + dht11_data[1] + dht11_data[2] + dht11_data[3];
    if (checksum != dht11_data[4]) {
        ESP_LOGW(TAG_DHT11, "校验失败: sum=0x%02X, checksum=0x%02X", checksum, dht11_data[4]);
        data->temp = -1;
        data->humi = -1;
        data->err  = 0x01;
        return ESP_FAIL;
    }

    /* 5. 解析数据 (DHT11 小数恒为0，来自数据手册) */
    data->humi = dht11_data[0];  /* 湿度整数 */
    data->temp = dht11_data[2];  /* 温度整数 */
    data->err  = 0;

    return ESP_OK;
}

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
 * @return 滤波后的 ADC 原始值 (0~4095), -1 表示异常
 */
static int adc_filter_sample(adc_channel_t channel)
{
    int samples[ADC_SAMPLE_COUNT];
    int all_zero = 1, all_max = 1;

    for (int i = 0; i < ADC_SAMPLE_COUNT; i++) {
        int raw = 0;
        /* API: adc_oneshot_read(handle, channel, &out_raw)
         * 来自 esp_adc/include/esp_adc/adc_oneshot.h:89 */
        esp_err_t ret = adc_oneshot_read(s_adc1_handle, channel, &raw);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG_PHOTO, "ADC 读取失败: %s", esp_err_to_name(ret));
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
    /* API: adc_oneshot_new_unit(init_config, ret_unit)
     * 来自 esp_adc/include/esp_adc/adc_oneshot.h:57 */
    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = PHOTO_ADC_UNIT,  /* ADC_UNIT_1 */
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg, &s_adc1_handle));

    /* ---- 2. 配置 ADC1_CH4 (GPIO20): 12 位精度, 12dB 衰减 (满量程 ~3.3V) ---- */
    /* API: adc_oneshot_config_channel(handle, channel, config)
     * 来自 esp_adc/include/esp_adc/adc_oneshot.h:72
     * ADC_ATTEN_DB_12: 满量程 ~3.3V
     * 来自 hal/include/hal/adc_types.h:50
     * ADC_BITWIDTH_12: 12位箾ADC输出 (0~4095)
     * 来自 hal/include/hal/adc_types.h:62 */
    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten    = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc1_handle, PHOTO_ADC_CHAN, &chan_cfg));

    /* ---- 3. 配置 DO 引脚 (GPIO23) 为数字输入 ---- */
    /* 使用说明书: DO 为 LM393 比较器输出, TTL 电平 */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << PHOTO_DO_GPIO),
        .mode          = GPIO_MODE_INPUT,
        .pull_up_en    = GPIO_PULLUP_DISABLE,
        .pull_down_en  = GPIO_PULLDOWN_DISABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    ESP_LOGI(TAG_PHOTO, "光敏电阻传感器初始化完成 (AO=GPIO%d/ADC1_CH4, DO=GPIO%d)",
             PHOTO_AO_GPIO, PHOTO_DO_GPIO);
    return ESP_OK;
}

esp_err_t photo_sensor_read(photo_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* ---- 读取 AO 模拟量 (ADC1_CH4/GPIO20) ---- */
    int filtered = adc_filter_sample(PHOTO_ADC_CHAN);
    if (filtered < 0) {
        /* ADC 异常: 全零或全满 (REQUIREMENT.md 5.7: err |= 0x08) */
        data->light_raw = -1;
        data->err       = 0x08;
        ESP_LOGE(TAG_PHOTO, "光敏 ADC 采样异常 (全零或全满)");
    } else {
        /* 直接返回滤波后的原始 ADC 值, 范围 0~4095 */
        data->light_raw = filtered;
        data->err       = 0;
    }

    /* ---- 读取 DO 数字量 (GPIO23) ---- */
    /* 使用说明书: 低于阈值→高电平(1), 超过阈值→低电平(0) */
    data->do_level = gpio_get_level(PHOTO_DO_GPIO);

    return ESP_OK;
}
