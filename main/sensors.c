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
static const char *TAG_DS18B20 = "ds18b20";
static const char *TAG_MQ135 = "mq135";
static const char *TAG_PHOTO = "photo_sensor";

/* ADC1 单元句柄 (MQ-135 与光敏共用) */
static adc_oneshot_unit_handle_t s_adc1_handle;
static bool s_adc1_inited = false;

/* ==================== DHT11 内部函数 ==================== */

/* DHT11 忙等待超时次数 (每次 ~10us, 共 ~200us) */
#define DHT11_TIMEOUT_LOOPS  20

/**
 * @brief 带超时的 GPIO 电平变化等待 (中断已关闭时使用)
 *
 * @param target  等待该电平结束 (0=等变高, 1=等变低)
 * @param loops   最大轮询次数 (超时则返回 -1)
 * @return 0 成功, -1 超时
 */
static int dht11_wait_level(int target, int loops)
{
    for (int n = 0; n < loops; n++) {
        if (gpio_get_level(DHT11_DATA_GPIO) != target) {
            return 0;  /* 电平已翻转, 正常 */
        }
        esp_rom_delay_us(10);
    }
    return -1;  /* 超时: GPIO 卡死 */
}

/**
 * @brief DHT11 复位并检测响应 (来自51参考代码 DHT11_ReadData)
 *
 * @return 0 成功, -1 无响应/超时
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

    /* 5. 等待响应低电平结束 (~80us) + 高电平结束 (~80us) */
    portDISABLE_INTERRUPTS();
    if (dht11_wait_level(0, DHT11_TIMEOUT_LOOPS) != 0) {
        portENABLE_INTERRUPTS();
        ESP_LOGW(TAG_DHT11, "响应低电平超时");
        return -1;
    }
    if (dht11_wait_level(1, DHT11_TIMEOUT_LOOPS) != 0) {
        portENABLE_INTERRUPTS();
        ESP_LOGW(TAG_DHT11, "响应高电平超时");
        return -1;
    }
    portENABLE_INTERRUPTS();

    return 0;
}

/**
 * @brief DHT11 读取一个字节 (来自51参考代码 DHT11_ReadData)
 *
 * @param ok 输出参数: 1=成功, 0=超时
 * @return 读取到的字节 (ok=0 时返回值无效)
 */
static uint8_t dht11_read_byte(int *ok)
{
    uint8_t byte = 0;
    *ok = 1;

    for (int i = 0; i < 8; i++) {
        /* 等待数据前置信号50us低电平结束 */
        portDISABLE_INTERRUPTS();
        if (dht11_wait_level(0, DHT11_TIMEOUT_LOOPS) != 0) {
            portENABLE_INTERRUPTS();
            ESP_LOGW(TAG_DHT11, "bit%d 低电平超时", i);
            *ok = 0;
            return 0;
        }

        /* 等待40us左右判断是1还是0 */
        esp_rom_delay_us(40);

        /* 如果是高电平 → 数据1; 低电平 → 数据0 */
        if (gpio_get_level(DHT11_DATA_GPIO) == 1) {
            byte |= (0x80 >> i);
            /* 等待高电平结束 */
            if (dht11_wait_level(1, DHT11_TIMEOUT_LOOPS) != 0) {
                portENABLE_INTERRUPTS();
                ESP_LOGW(TAG_DHT11, "bit%d 高电平超时", i);
                *ok = 0;
                return 0;
            }
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

/**
 * @brief DHT11 GPIO 复位: 重新初始为开漏模式并拉高总线
 *
 * 任何 while-polling 超时后调用, 清除卡死状态
 */
static void dht11_gpio_reset(void)
{
    /* 切回推挽输出, 强制拉低→拉高→再切回开漏 */
    gpio_set_direction(DHT11_DATA_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(DHT11_DATA_GPIO, 0);
    esp_rom_delay_us(100);
    gpio_set_level(DHT11_DATA_GPIO, 1);
    gpio_set_direction(DHT11_DATA_GPIO, GPIO_MODE_INPUT_OUTPUT_OD);
}

esp_err_t dht11_read(dht11_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t dht11_data[5];
    int byte_ok;

    /* 1. 复位并检测响应 */
    if (dht11_reset() != 0) {
        dht11_gpio_reset();
        data->temp = -1;
        data->humi = -1;
        data->err  = 0x01;
        return ESP_FAIL;
    }

    /* 2. 读取 5 字节数据 (带超时保护) */
    for (int j = 0; j < 5; j++) {
        dht11_data[j] = dht11_read_byte(&byte_ok);
        if (!byte_ok) {
            /* 位读取超时: 复位 GPIO 后返回失败 */
            dht11_gpio_reset();
            data->temp = -1;
            data->humi = -1;
            data->err  = 0x01;
            return ESP_FAIL;
        }
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

/* ==================== DS18B20 内部函数 ==================== */

/**
 * @brief 1-Wire 总线初始化 (来自参考代码 One_Wire_Init)
 *
 * @return 0 成功, 1 无响应
 */
static int one_wire_init(void)
{
    /* 1. 拉低总线 500µs */
    gpio_set_level(DS18B20_DATA_GPIO, 0);
    portDISABLE_INTERRUPTS();
    esp_rom_delay_us(500);
    portENABLE_INTERRUPTS();

    /* 2. 释放总线 */
    gpio_set_level(DS18B20_DATA_GPIO, 1);

    /* 3. 等待 100µs 后检测响应 */
    portDISABLE_INTERRUPTS();
    esp_rom_delay_us(100);
    int ack = gpio_get_level(DS18B20_DATA_GPIO);

    /* 4. 延时 400µs 让时序完整 (总时长 ≥480µs) */
    esp_rom_delay_us(400);
    portENABLE_INTERRUPTS();

    return ack;
}

/**
 * @brief 1-Wire 写一个字节 (来自参考代码 One_Wire_WriteData)
 *
 * @param byte 要写入的字节
 */
static void one_wire_write_byte(uint8_t byte)
{
    for (int i = 0; i < 8; i++) {
        if (byte & (0x01 << i)) {
            /* 写1: 拉低 10µs → 释放 */
            gpio_set_level(DS18B20_DATA_GPIO, 0);
            portDISABLE_INTERRUPTS();
            esp_rom_delay_us(10);
            portENABLE_INTERRUPTS();
            gpio_set_level(DS18B20_DATA_GPIO, 1);

            portDISABLE_INTERRUPTS();
            esp_rom_delay_us(60);
            portENABLE_INTERRUPTS();
        } else {
            /* 写0: 拉低 60µs → 释放 */
            gpio_set_level(DS18B20_DATA_GPIO, 0);
            portDISABLE_INTERRUPTS();
            esp_rom_delay_us(60);
            portENABLE_INTERRUPTS();
            gpio_set_level(DS18B20_DATA_GPIO, 1);

            portDISABLE_INTERRUPTS();
            esp_rom_delay_us(10);
            portENABLE_INTERRUPTS();
        }
    }
}

/**
 * @brief 1-Wire 读一个字节 (来自参考代码 One_Wire_ReadData)
 *
 * @return 读取到的字节
 */
static uint8_t one_wire_read_byte(void)
{
    uint8_t byte = 0;

    for (int i = 0; i < 8; i++) {
        /* 1. 拉低总线 5µs */
        gpio_set_level(DS18B20_DATA_GPIO, 0);
        portDISABLE_INTERRUPTS();
        esp_rom_delay_us(5);
        portENABLE_INTERRUPTS();

        /* 2. 释放总线 */
        gpio_set_level(DS18B20_DATA_GPIO, 1);

        /* 3. 等待 5µs 后读取 */
        portDISABLE_INTERRUPTS();
        esp_rom_delay_us(5);
        if (gpio_get_level(DS18B20_DATA_GPIO)) {
            byte |= (0x01 << i);
        }
        portENABLE_INTERRUPTS();

        /* 4. 等待 60µs 让 slot 结束 */
        portDISABLE_INTERRUPTS();
        esp_rom_delay_us(60);
        portENABLE_INTERRUPTS();
    }

    return byte;
}

/* ==================== DS18B20 公共函数 ==================== */

esp_err_t ds18b20_init(void)
{
    /* 配置 DATA 引脚为开漏输入输出模式 (1-Wire总线要求) */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << DS18B20_DATA_GPIO),
        .mode          = GPIO_MODE_INPUT_OUTPUT_OD,  /* 开漏模式 */
        .pull_up_en    = GPIO_PULLUP_DISABLE,       /* 外部接4.7K上拉电阻 */
        .pull_down_en  = GPIO_PULLDOWN_DISABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    /* 初始拉高总线 */
    gpio_set_level(DS18B20_DATA_GPIO, 1);

    ESP_LOGI(TAG_DS18B20, "DS18B20 初始化完成 (DATA=GPIO%d)", DS18B20_DATA_GPIO);
    return ESP_OK;
}

esp_err_t ds18b20_read(ds18b20_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t low_byte, high_byte;

    /* 1. 初始化并检测响应 */
    if (one_wire_init() != 0) {
        ESP_LOGW(TAG_DS18B20, "DS18B20 无响应");
        data->temp = -999.0;
        data->err  = 0x02;
        return ESP_FAIL;
    }

    /* 2. 发送 Skip ROM 命令 (0xCC) - 单设备时跳过ROM匹配 */
    one_wire_write_byte(0xCC);

    /* 3. 发送温度转换命令 (0x44) */
    one_wire_write_byte(0x44);

    /* 4. 等待转换完成 (12位分辨率最大 750ms) */
    vTaskDelay(pdMS_TO_TICKS(800));

    /* 5. 再次初始化 */
    if (one_wire_init() != 0) {
        ESP_LOGW(TAG_DS18B20, "DS18B20 第二次初始化无响应");
        data->temp = -999.0;
        data->err  = 0x02;
        return ESP_FAIL;
    }

    /* 6. 发送 Skip ROM 命令 */
    one_wire_write_byte(0xCC);

    /* 7. 发送读暂存器命令 (0xBE) */
    one_wire_write_byte(0xBE);

    /* 8. 读取温度数据 (低字节在前, 高字节在后) */
    low_byte  = one_wire_read_byte();
    high_byte = one_wire_read_byte();

    /* 9. 计算温度值: (高字节 << 8 | 低字节) / 16.0 */
    int temp_raw = (high_byte << 8) | low_byte;
    data->temp = temp_raw / 16.0;
    data->err  = 0;

    ESP_LOGI(TAG_DS18B20, "DS18B20: 温度=%.4f°C (raw=0x%04X)", data->temp, temp_raw);

    return ESP_OK;
}

/* ==================== ADC1 共享初始化 ==================== */

/**
 * @brief 确保 ADC1 单元已初始化 (光敏与 MQ-135 共享 ADC1)
 *
 * 多次调用安全: 第二次及以后调用直接返回
 * API: adc_oneshot_new_unit() 来自 esp_adc/include/esp_adc/adc_oneshot.h:57
 */
static void adc1_shared_init(void)
{
    if (s_adc1_inited) {
        return;
    }

    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg, &s_adc1_handle));
    s_adc1_inited = true;
}

/* adc_filter_sample 前置声明 (定义在后, mq135_read 先调用) */
static int adc_filter_sample(adc_channel_t channel);

/* ==================== MQ-135 公共函数 ==================== */

/* MQ-135 预热状态标志 (预热期间 DO 不可信) */
static bool s_mq135_warmed_up = false;

esp_err_t mq135_init(void)
{
    /* 1. 确保 ADC1 单元已初始化 (与光敏共享, 只初始化一次) */
    adc1_shared_init();

    /* 2. 配置 ADC1_CH5 (GPIO21): 12 位精度, 12dB 衰减
     * API: adc_oneshot_config_channel() 来自 esp_adc/include/esp_adc/adc_oneshot.h:72 */
    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten    = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc1_handle, MQ135_ADC_CHAN, &chan_cfg));

    /* 3. 配置 DO 引脚 (GPIO22) 为数字输入
     * 模块基础参数: DO 为 TTL 低电平有效 (超阈值→0, 信号灯亮→1)
     * 需电平转换 5V→3.3V (REQUIREMENT.md 3.2) */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << MQ135_DO_GPIO),
        .mode          = GPIO_MODE_INPUT,
        .pull_up_en    = GPIO_PULLUP_DISABLE,
        .pull_down_en  = GPIO_PULLDOWN_DISABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    /* 4. 预热到 DO 稳定为 1 (空气质量正常) 或超时
     * - 技术手册要求预热 ≥3 分钟才能稳定
     * - 加热丝加热过程中 DO 可能输出不稳定，直到敏感电阻达到稳态
     * - 连续 5 次读数为 1 才认为稳定，避免噪声抖动
     * - 最大等待 60 秒，防止异常情况死等
     * 注意: 使用静态变量 s_mq135_warmed_up，不访问 g_sensor_data 避免与 ESP-Hosted 冲突 */
    s_mq135_warmed_up = false;  /* 重置预热状态 */
    ESP_LOGI(TAG_MQ135, "MQ-135 预热中，等待 DO 稳定...");
    int stable_count = 0;
    int warmup_timeout = 600;  /* 60 秒超时 (600 x 100ms) */

    while (warmup_timeout-- > 0) {
        int do_level = gpio_get_level(MQ135_DO_GPIO);

        if (do_level == 1) {
            stable_count++;
            if (stable_count >= 5) {
                /* 连续 5 次稳定 → 预热完成 */
                break;
            }
        } else {
            stable_count = 0;  /* 复位计数 */
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }

    if (warmup_timeout > 0) {
        ESP_LOGI(TAG_MQ135, "MQ-135 预热完成 (约耗时 %ds)", (600 - warmup_timeout) / 10);
    } else {
        ESP_LOGW(TAG_MQ135, "MQ-135 预热超时 (60s)，继续运行");
    }

    s_mq135_warmed_up = true;
    ESP_LOGI(TAG_MQ135, "MQ-135 初始化完成 (AO=GPIO%d/ADC1_CH5, DO=GPIO%d)",
             MQ135_AO_GPIO, MQ135_DO_GPIO);
    return ESP_OK;
}

bool mq135_is_warmed_up(void)
{
    return s_mq135_warmed_up;
}

esp_err_t mq135_read(mq135_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* 读取 AO 模拟量 (ADC1_CH5/GPIO21), 复用 adc_filter_sample() */
    int filtered = adc_filter_sample(MQ135_ADC_CHAN);
    if (filtered < 0) {
        /* ADC 异常: 全零或全满 (REQUIREMENT.md 5.7: err |= 0x04) */
        data->ao_raw  = -1;
        data->voltage = -1.0;
        data->err     = 0x04;
        ESP_LOGE(TAG_MQ135, "MQ-135 ADC 采样异常 (全零或全满)");
    } else {
        data->ao_raw  = filtered;
        data->voltage = filtered * 3.3f / 4095.0f;  /* REQUIREMENT.md 5.4 */
        data->err     = 0;
    }

    /* 读取 DO 数字量 (GPIO22)
     * 模块基础参数: TTL 低电平有效, 超阈值→0(报警), 正常→1 */
    data->do_level = gpio_get_level(MQ135_DO_GPIO);

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
    /* ---- 1. 确保 ADC1 单元已初始化 (与 MQ-135 共享, 只初始化一次) ---- */
    adc1_shared_init();

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

/* ==================== 蜂鸣器驱动 ==================== */

static const char *TAG_BUZZER = "buzzer";

esp_err_t buzzer_init(void)
{
    /* 配置 GPIO25 为推挽输出, 初始低电平 (静音)
     * 下拉使能: 上电/复位期间保持低电平, 防止误触发蜂鸣器
     * 驱动电路: GPIO25 → 1KΩ → S8050基极 (REQUIREMENT.md 3.2) */
    gpio_config_t io_conf = {
        .pin_bit_mask  = (1ULL << BUZZER_GPIO),
        .mode          = GPIO_MODE_OUTPUT,
        .pull_up_en    = GPIO_PULLUP_DISABLE,
        .pull_down_en  = GPIO_PULLDOWN_ENABLE,
        .intr_type     = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    /* 确保初始为低电平 */
    gpio_set_level(BUZZER_GPIO, 0);

    ESP_LOGI(TAG_BUZZER, "蜂鸣器初始化完成 (GPIO%d)", BUZZER_GPIO);
    return ESP_OK;
}

esp_err_t buzzer_set(int on)
{
    /* 有源蜂鸣器: 高电平 → S8050导通 → 蜂鸣器鸣叫 */
    gpio_set_level(BUZZER_GPIO, on ? 1 : 0);
    return ESP_OK;
}
