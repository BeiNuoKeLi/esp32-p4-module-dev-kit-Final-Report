/*
 * SPDX-FileCopyrightText: 2010-2022 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: CC0-1.0
 */

#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_wifi_remote.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "sensors.h"
#include "oled_ssd1306.h"
#include "udp_sender.h"
#include "sim_poll.h"
#include "lwip/sockets.h"

/* ================== Wi-Fi 配置（通过 menuconfig 设置）================== */
#define WIFI_SSID   CONFIG_EXAMPLE_WIFI_SSID
#define WIFI_PASS   CONFIG_EXAMPLE_WIFI_PASSWORD

static const char *TAG = "main";

/* ================== 全局共享数据 (各传感器任务写入, OLED任务读取) ================== */
sensor_shared_t g_sensor_data = {0};
SemaphoreHandle_t g_sensor_mutex = NULL;

/* ================== Wi-Fi 事件回调 ================== */
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "Connecting to AP...");
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *disconn = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGW(TAG, "Disconnected, reason: %d, reconnecting...", disconn->reason);
        /* 更新 Wi-Fi 状态到共享数据 */
        if (g_sensor_mutex && xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.wifi_connected = 0;
            xSemaphoreGive(g_sensor_mutex);
        }
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        /* 更新 Wi-Fi 状态到共享数据 */
        if (g_sensor_mutex && xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.wifi_connected = 1;
            xSemaphoreGive(g_sensor_mutex);
        }
    }
}

/* ================== Wi-Fi 初始化 ================== */
static __attribute__((unused)) void wifi_init_sta(void)
{
    /* 1. 初始化 NVS */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* 2. 初始化 TCP/IP 网络栈 */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    /* 3. 初始化 Wi-Fi（底层通过 SDIO → ESP32-C6） */
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* 4. 注册事件回调 */
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    /* 5. 配置 SSID / 密码 */
    wifi_config_t wifi_cfg = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Wi-Fi STA init done, SSID: %s", WIFI_SSID);
}

/* ================== NVS 报警配置持久化 ================== */
#define NVS_ALARM_NS   "alarm_cfg"   /*!< NVS 命名空间（≤15字符） */

/**
 * @brief 从 NVS 加载报警配置到 g_sensor_data
 *
 * 先写入编译期默认值，再尝试从 NVS 覆盖。
 * 命名空间不存在时静默跳过，全用默认值。
 * 必须在 buzzer_task 创建前调用！
 */
static void load_alarm_config_from_nvs(void)
{
    /* 1. 先写入编译期默认值 */
    g_sensor_data.mq135_alarm_src   = ALARM_SRC_DO;
    g_sensor_data.photo_alarm_src   = ALARM_SRC_DO;
    g_sensor_data.mq135_ao_dir      = AO_TRIG_ABOVE;
    g_sensor_data.photo_ao_dir      = AO_TRIG_BELOW;
    g_sensor_data.mq135_ao_threshold = ALARM_DEFAULT_MQ135_AO_THR_RAW;
    g_sensor_data.photo_ao_threshold = ALARM_DEFAULT_PHOTO_AO_THR;
    g_sensor_data.dht11_temp_high    = ALARM_TEMP_HIGH_DHT11;
    g_sensor_data.dht11_humi_high    = ALARM_HUMI_HIGH;
    g_sensor_data.ds18b20_temp_high  = ALARM_TEMP_HIGH_DS18B20;
    g_sensor_data.temp_humi_alarm_enabled = 1;

    /* 2. 尝试从 NVS 读取覆盖 */
    nvs_handle_t h;
    if (nvs_open(NVS_ALARM_NS, NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG, "NVS: 命名空间 '%s' 不存在，使用默认配置", NVS_ALARM_NS);
        return;
    }

    uint8_t  u8_val;
    uint32_t u32_val;
    int32_t  i32_val;

    /* ── 读取并做范围验证, 拒绝 flash 中的损坏值 ── */
    if (nvs_get_u8(h, "mq_mode", &u8_val) == ESP_OK && u8_val <= 1)
        g_sensor_data.mq135_alarm_src = (alarm_source_t)u8_val;
    if (nvs_get_u32(h, "mq_ao_raw", &u32_val) == ESP_OK && u32_val >= 0 && u32_val <= 4095)
        g_sensor_data.mq135_ao_threshold = (int)u32_val;
    if (nvs_get_u8(h, "mq_ao_dir", &u8_val) == ESP_OK && u8_val <= 1)
        g_sensor_data.mq135_ao_dir = (ao_trigger_dir_t)u8_val;

    if (nvs_get_u8(h, "ph_mode", &u8_val) == ESP_OK && u8_val <= 1)
        g_sensor_data.photo_alarm_src = (alarm_source_t)u8_val;
    if (nvs_get_u32(h, "ph_ao_thr", &u32_val) == ESP_OK && u32_val >= 100 && u32_val <= 4095)
        g_sensor_data.photo_ao_threshold = (int)u32_val;
    if (nvs_get_u8(h, "ph_ao_dir", &u8_val) == ESP_OK && u8_val <= 1)
        g_sensor_data.photo_ao_dir = (ao_trigger_dir_t)u8_val;

    if (nvs_get_i32(h, "dht_t_hi", &i32_val) == ESP_OK && i32_val >= 10 && i32_val <= 60)
        g_sensor_data.dht11_temp_high = (int)i32_val;
    if (nvs_get_i32(h, "dht_h_hi", &i32_val) == ESP_OK && i32_val >= 30 && i32_val <= 100)
        g_sensor_data.dht11_humi_high = (int)i32_val;
    if (nvs_get_i32(h, "ds_t_hi", &i32_val) == ESP_OK && i32_val >= 0 && i32_val <= 800)
        g_sensor_data.ds18b20_temp_high = (float)i32_val / 10.0f;
    if (nvs_get_u8(h, "temp_en", &u8_val) == ESP_OK && u8_val <= 1)
        g_sensor_data.temp_humi_alarm_enabled = (int)u8_val;

    nvs_close(h);
    ESP_LOGI(TAG, "NVS: 报警配置已加载 | mq_src=%d mq_thr=%draw ph_src=%d ph_thr=%d dht_t=%d dht_h=%d ds_t=%.1f temp_en=%d",
             g_sensor_data.mq135_alarm_src, g_sensor_data.mq135_ao_threshold,
             g_sensor_data.photo_alarm_src, g_sensor_data.photo_ao_threshold,
             g_sensor_data.dht11_temp_high, g_sensor_data.dht11_humi_high,
             g_sensor_data.ds18b20_temp_high, g_sensor_data.temp_humi_alarm_enabled);
}

/**
 * @brief 将当前报警配置保存到 NVS（在收到 config 命令时调用）
 */
void save_alarm_config_to_nvs(void)
{
    /* 快照 g_sensor_data 的值（持锁），然后无锁写 NVS
     * — 避免长时间持锁阻塞传感器任务，同时防止 sim_poll/udp_sim 并发写 NVS 导致损坏 */
    uint8_t  mq_mode, mq_ao_dir, ph_mode, ph_ao_dir, temp_en;
    uint32_t mq_ao_thr, ph_ao_thr;
    int32_t  dht_t_hi, dht_h_hi, ds_t_hi;

    if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        mq_mode   = (uint8_t)g_sensor_data.mq135_alarm_src;
        mq_ao_thr = (uint32_t)g_sensor_data.mq135_ao_threshold;
        mq_ao_dir = (uint8_t)g_sensor_data.mq135_ao_dir;
        ph_mode   = (uint8_t)g_sensor_data.photo_alarm_src;
        ph_ao_thr = (uint32_t)g_sensor_data.photo_ao_threshold;
        ph_ao_dir = (uint8_t)g_sensor_data.photo_ao_dir;
        dht_t_hi  = (int32_t)g_sensor_data.dht11_temp_high;
        dht_h_hi  = (int32_t)g_sensor_data.dht11_humi_high;
        ds_t_hi   = (int32_t)(g_sensor_data.ds18b20_temp_high * 10.0f);
        temp_en   = (uint8_t)g_sensor_data.temp_humi_alarm_enabled;
        xSemaphoreGive(g_sensor_mutex);
    } else {
        ESP_LOGE(TAG, "NVS: 无法获取传感器锁, 跳过持久化");
        return;
    }

    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_ALARM_NS, NVS_READWRITE, &h);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "NVS: 无法打开 '%s' 进行写入: %d", NVS_ALARM_NS, ret);
        return;
    }

    nvs_set_u8(h,  "mq_mode",   mq_mode);
    nvs_set_u32(h, "mq_ao_raw", mq_ao_thr);
    nvs_set_u8(h,  "mq_ao_dir", mq_ao_dir);
    nvs_set_u8(h,  "ph_mode",   ph_mode);
    nvs_set_u32(h, "ph_ao_thr", ph_ao_thr);
    nvs_set_u8(h,  "ph_ao_dir", ph_ao_dir);
    nvs_set_i32(h, "dht_t_hi",  dht_t_hi);
    nvs_set_i32(h, "dht_h_hi",  dht_h_hi);
    nvs_set_i32(h, "ds_t_hi",   ds_t_hi);
    nvs_set_u8(h,  "temp_en",   temp_en);

    ret = nvs_commit(h);
    nvs_close(h);

    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "NVS: 报警配置已持久化");
    } else {
        ESP_LOGE(TAG, "NVS: 提交失败: %d", ret);
    }
}

/* ================== MQ-135 空气质量传感器读取任务 ================== */
static void mq135_task(void *arg)
{
    mq135_data_t data;

    while (1) {
        esp_err_t ret = mq135_init();
        if (ret == ESP_OK) break;
        ESP_LOGW(TAG, "MQ-135 初始化失败, 5s 后重试");
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.mq135_err = 1;
            xSemaphoreGive(g_sensor_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }

    while (1) {
        /* ---- 仿真模式分支: 跳过硬件读取, 直接注入仿真值 ---- */
        if (g_sensor_data.sim_active) {
            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                g_sensor_data.mq135_voltage = g_sensor_data.sim_mq135_v;
                /* 从电压反算 raw 供 AO 阈值比较 (统一单位) */
                g_sensor_data.mq135_ao_raw  = (int)(g_sensor_data.sim_mq135_v * 4095.0f / 3.3f);
                g_sensor_data.mq135_do      = g_sensor_data.sim_mq135_do;
                g_sensor_data.mq135_err     = 0;
                xSemaphoreGive(g_sensor_mutex);
            }
            ESP_LOGI(TAG, "MQ-135 [SIM]: raw=%d | DO=%d",
                     g_sensor_data.mq135_ao_raw, g_sensor_data.sim_mq135_do);
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }
        /* ---- 真实传感器读取 ---- */
        mq135_read(&data);

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.mq135_ao_raw  = data.ao_raw;
            g_sensor_data.mq135_voltage = data.voltage;
            g_sensor_data.mq135_do      = data.do_level;
            g_sensor_data.mq135_err     = data.err;
            xSemaphoreGive(g_sensor_mutex);
        }

        if (data.err) {
            ESP_LOGW(TAG, "MQ-135: 读取失败, err=0x%02X", data.err);
        } else {
            ESP_LOGI(TAG, "MQ-135: AO_raw=%d | V=%.2fV | DO=%d (%s)",
                     data.ao_raw, data.voltage, data.do_level,
                     data.do_level ? "正常" : "超阈值");
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

/* ================== DS18B20 温度传感器读取任务 ================== */
static void ds18b20_task(void *arg)
{
    ds18b20_data_t data;

    while (1) {
        esp_err_t ret = ds18b20_init();
        if (ret == ESP_OK) break;
        ESP_LOGW(TAG, "DS18B20 初始化失败, 5s 后重试");
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.ds18b20_err = 1;
            xSemaphoreGive(g_sensor_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }

    while (1) {
        /* ---- 仿真模式分支 ---- */
        if (g_sensor_data.sim_active) {
            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                g_sensor_data.ds18b20_temp = g_sensor_data.sim_ds18b20_t;
                g_sensor_data.ds18b20_err  = 0;
                xSemaphoreGive(g_sensor_mutex);
            }
            ESP_LOGI(TAG, "DS18B20 [SIM]: 温度=%.4f°C", g_sensor_data.sim_ds18b20_t);
            vTaskDelay(pdMS_TO_TICKS(3000));
            continue;
        }
        /* ---- 真实传感器读取 ---- */
        ds18b20_read(&data);

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.ds18b20_temp = data.temp;
            g_sensor_data.ds18b20_err  = data.err;
            xSemaphoreGive(g_sensor_mutex);
        }

        if (data.err) {
            ESP_LOGW(TAG, "DS18B20: 读取失败, err=0x%02X", data.err);
        } else {
            ESP_LOGI(TAG, "DS18B20: 温度=%.4f°C", data.temp);
        }

        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}

/* ================== DHT11 温湿度传感器读取任务 ================== */
static void dht11_task(void *arg)
{
    dht11_data_t data;

    while (1) {
        esp_err_t ret = dht11_init();
        if (ret == ESP_OK) break;
        ESP_LOGW(TAG, "DHT11 初始化失败, 5s 后重试");
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.dht11_err = 1;
            xSemaphoreGive(g_sensor_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }

    while (1) {
        /* ---- 仿真模式分支 ---- */
        if (g_sensor_data.sim_active) {
            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                g_sensor_data.dht11_temp = g_sensor_data.sim_dht11_t;
                g_sensor_data.dht11_humi = g_sensor_data.sim_dht11_h;
                g_sensor_data.dht11_err  = 0;
                xSemaphoreGive(g_sensor_mutex);
            }
            ESP_LOGI(TAG, "DHT11 [SIM]: 温度=%d°C | 湿度=%d%%RH",
                     g_sensor_data.sim_dht11_t, g_sensor_data.sim_dht11_h);
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }
        /* ---- 真实传感器读取 (带重试) ---- */
        data.err = 1;
        for (int retry = 0; retry < 3 && data.err != 0; retry++) {
            dht11_read(&data);
            if (data.err == 0) break;
            vTaskDelay(pdMS_TO_TICKS(100));  /* 让总线恢复, 避让 WiFi 突发 */
        }

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.dht11_temp = data.temp;
            g_sensor_data.dht11_humi = data.humi;
            g_sensor_data.dht11_err  = data.err;
            xSemaphoreGive(g_sensor_mutex);
        }

        if (data.err) {
            ESP_LOGW(TAG, "DHT11: 读取失败(重试3次均失败), err=0x%02X", data.err);
        } else {
            ESP_LOGI(TAG, "DHT11: 温度=%d°C | 湿度=%d%%RH", data.temp, data.humi);
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

/* ================== 光敏电阻传感器读取任务 ================== */
static void photo_sensor_task(void *arg)
{
    photo_data_t data;

    while (1) {
        esp_err_t ret = photo_sensor_init();
        if (ret == ESP_OK) break;
        ESP_LOGW(TAG, "光敏电阻传感器初始化失败, 5s 后重试");
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.photo_err = 1;
            xSemaphoreGive(g_sensor_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }

    while (1) {
        /* ---- 仿真模式分支 ---- */
        if (g_sensor_data.sim_active) {
            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                g_sensor_data.photo_raw = g_sensor_data.sim_photo_raw;
                g_sensor_data.photo_do  = g_sensor_data.sim_photo_do;
                g_sensor_data.photo_err = 0;
                xSemaphoreGive(g_sensor_mutex);
            }
            ESP_LOGI(TAG, "光敏 [SIM]: raw=%d | DO=%d",
                     g_sensor_data.sim_photo_raw, g_sensor_data.sim_photo_do);
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }
        /* ---- 真实传感器读取 ---- */
        photo_sensor_read(&data);

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.photo_raw = data.light_raw;
            g_sensor_data.photo_do  = data.do_level;
            g_sensor_data.photo_err = data.err;
            xSemaphoreGive(g_sensor_mutex);
        }

        ESP_LOGI(TAG, "光敏: AO_raw=%d | DO=%d (%s)",
                 data.light_raw, data.do_level,
                 data.do_level ? "正常" : "超阈值");

        if (data.err) {
            ESP_LOGW(TAG, "光敏传感器错误: 0x%02X", data.err);
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

/* ================== OLED 显示刷新任务 ================== */
static void oled_display_task(void *arg)
{
    sensor_shared_t local = {0};

    /* 等待其他传感器任务初始化完成 */
    vTaskDelay(pdMS_TO_TICKS(3000));

    while (1) {
        /* 持锁读取共享数据 */
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
            memcpy(&local, &g_sensor_data, sizeof(sensor_shared_t));
            xSemaphoreGive(g_sensor_mutex);
        }

        /* ---- 行 0: 标题 ---- */
        oled_show_line(0, "  Smart Monitor   ");

        /* ---- 行 1: DHT11 温湿度 ---- */
        if (local.dht11_err) {
            oled_show_line(1, "DHT11: ERR         ");
        } else {
            oled_show_line(1, "DHT11:T=%-2dC H=%-2d%%",
                           local.dht11_temp, local.dht11_humi);
        }

        /* ---- 行 2: DS18B20 高精度温度 ---- */
        if (local.ds18b20_err) {
            oled_show_line(2, "DS18B20: ERR       ");
        } else {
            oled_show_line(2, "DS18B20: %.4f C",
                           local.ds18b20_temp);
        }

        /* ---- 行 3: MQ-135 空气质量 ---- */
        /* 预热期间显示 "Warming..."，与 buzzer_task 判断逻辑保持一致 */
        if (!mq135_is_warmed_up()) {
            oled_show_line(3, "MQ135: Warming...  ");
        } else if (local.mq135_err) {
            oled_show_line(3, "MQ135: ERR         ");
        } else {
            oled_show_line(3, "MQ135: %-4draw %s",
                           local.mq135_ao_raw,
                           local.mq135_do ? "OK" : "ALM");
        }

        /* ---- 行 4: 光敏电阻 ---- */
        if (local.photo_err) {
            oled_show_line(4, "Light: ERR         ");
        } else {
            oled_show_line(4, "Light: %-4draw %s",
                           local.photo_raw,
                           local.photo_do ? "OK" : "ALM");
        }

        /* ---- 行 5: 报警级别显示 (v2.0 分级) ---- */
        int err_sum = local.dht11_err | local.ds18b20_err | local.mq135_err | local.photo_err;
        const char *level_str = "OFF";
        switch (local.alarm_level) {
            case ALARM_WARNING:   level_str = "L1 YuJing";  break;
            case ALARM_SEVERE:    level_str = "L2 YanZhong";break;
            case ALARM_EMERGENCY: level_str = "L3 JinJi";   break;
            default:              level_str = "OFF";         break;
        }
        oled_show_line(5, "Alrt:%-10s E:%02X", level_str, err_sum);

        /* ---- 行 6: 蜂鸣器 + Wi-Fi 状态 ---- */
        oled_show_line(6, "Buz:%s  WiFi:%s",
                       local.buzzer_on ? "ON " : "OFF",
                       local.wifi_connected ? "OK  " : "DOWN");

        /* ---- 行 7: 风扇+LED 状态 (v2.0: 风扇仅由 A 类源控制) ---- */
        oled_show_line(7, "Fan:%s LED:%s",
                       local.fan_on ? "ON " : "OFF",
                       local.alarm_level > ALARM_OFF ? "RED" : "GRN");

        vTaskDelay(pdMS_TO_TICKS(1000));  /* 每 1 秒刷新一次 */
    }
}

/* ================== 蜂鸣器报警控制任务 (v2.0 分级报警) ================== */
/**
 * @brief 分级报警控制任务
 *
 * 报警源分为两类:
 *   A 类 (风机关联): MQ-135 DO=0 / DHT11 高温 / DS18B20 高温 → 开风扇排风
 *   B 类 (仅提醒):   光敏 DO=0 / DHT11 高湿 → 声光提醒, 风扇不动
 *
 * 级别判定:
 *   L0 正常: 无任何触发
 *   L1 预警: 仅 B 类源触发, 风扇 OFF
 *   L2 严重: 单个 A 类源触发, 风扇 ON
 *   L3 紧急: 多个 A 类源触发 或 报警持续 >30s
 */
static void buzzer_task(void *arg)
{
    esp_err_t ret = buzzer_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "蜂鸣器初始化失败, 报警功能降级 (无声音)");
    }

    ret = led_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "LED初始化失败");
    }

    ret = relay_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "继电器初始化失败");
    }

    /* 确保初始状态: 全部关闭 */
    buzzer_set(0);
    led_set_red(0);
    led_set_green(0);
    relay_set(0);

    static TickType_t s_alarm_start_tick = 0;   /* 首次报警时刻 */
    static alarm_level_t s_prev_level = ALARM_OFF; /* 上一轮级别 */

    while (1) {
        /* ---- 1. 读传感器数据 + 运行时阈值 ---- */
        int mq135_do = 1, photo_do = 1;
        int dht11_temp = 0, dht11_humi = 0;
        float ds18b20_temp = 0.0f;
        int   mq135_ao_raw = 0;
        int   photo_raw = 0;
        alarm_source_t mq_src = ALARM_SRC_DO, ph_src = ALARM_SRC_DO;
        ao_trigger_dir_t mq_dir = AO_TRIG_ABOVE, ph_dir = AO_TRIG_BELOW;
        int   mq_ao_thr = 3100;
        int   ph_ao_thr = 1000;
        int   dht_t_hi = 35, dht_h_hi = 85;
        float ds_t_hi  = 35.0f;
        int   temp_humi_en = 1;

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            mq135_do       = mq135_is_warmed_up() ? g_sensor_data.mq135_do : 1;
            mq135_ao_raw   = g_sensor_data.mq135_ao_raw;
            photo_do       = g_sensor_data.photo_do;
            photo_raw      = g_sensor_data.photo_raw;
            dht11_temp     = g_sensor_data.dht11_temp;
            dht11_humi     = g_sensor_data.dht11_humi;
            ds18b20_temp   = g_sensor_data.ds18b20_temp;
            mq_src         = g_sensor_data.mq135_alarm_src;
            ph_src         = g_sensor_data.photo_alarm_src;
            mq_dir         = g_sensor_data.mq135_ao_dir;
            ph_dir         = g_sensor_data.photo_ao_dir;
            mq_ao_thr      = g_sensor_data.mq135_ao_threshold;
            ph_ao_thr      = g_sensor_data.photo_ao_threshold;
            dht_t_hi       = g_sensor_data.dht11_temp_high;
            dht_h_hi       = g_sensor_data.dht11_humi_high;
            ds_t_hi        = g_sensor_data.ds18b20_temp_high;
            temp_humi_en   = g_sensor_data.temp_humi_alarm_enabled;
            xSemaphoreGive(g_sensor_mutex);
        }

        /* ---- 2. 计算报警源分类 ---- */
        /* A 类: 毒气/高温 → 必须排风 */
        int cat_a_mq135 = 0;
        if (mq_src == ALARM_SRC_DO) {
            cat_a_mq135 = (!mq135_do) ? 1 : 0;                      /* DO 模式 */
        } else {
            /* AO 模式: 根据 ADC raw 阈值判定 (统一单位, 与光敏一致) */
            if (mq_dir == AO_TRIG_ABOVE) {
                cat_a_mq135 = (mq135_ao_raw >= mq_ao_thr) ? 1 : 0;
            } else {
                cat_a_mq135 = (mq135_ao_raw <= mq_ao_thr) ? 1 : 0;
            }
        }

        int cat_a_temp_dht = (temp_humi_en && dht11_temp >= dht_t_hi
                              && dht11_temp <= 50) ? 1 : 0;              /* DHT11 高温 */
        int cat_a_temp_ds  = (temp_humi_en && ds18b20_temp >= ds_t_hi
                              && ds18b20_temp <= 125.0f) ? 1 : 0;       /* DS18B20 高温 */
        int cat_a = cat_a_mq135 || cat_a_temp_dht || cat_a_temp_ds;
        int cat_a_count = cat_a_mq135 + cat_a_temp_dht + cat_a_temp_ds;

        /* B 类: 光照/湿度异常 → 仅提醒, 不排风 */
        int cat_b_photo = 0;
        if (ph_src == ALARM_SRC_DO) {
            cat_b_photo = (!photo_do) ? 1 : 0;                      /* DO 模式 */
        } else {
            /* AO 模式: 根据 ADC 阈值判定 */
            if (ph_dir == AO_TRIG_ABOVE) {
                cat_b_photo = (photo_raw >= ph_ao_thr) ? 1 : 0;
            } else {
                cat_b_photo = (photo_raw <= ph_ao_thr) ? 1 : 0;
            }
        }

        int cat_b_humi   = (temp_humi_en && dht11_humi >= dht_h_hi
                            && dht11_humi <= 90) ? 1 : 0;               /* DHT11 高湿 */
        int cat_b = cat_b_photo || cat_b_humi;

        int any_alarm = cat_a || cat_b;

        /* ---- 3. 判定报警级别 ---- */
        alarm_level_t level = ALARM_OFF;

        if (any_alarm) {
            /* 首次报警: 记录时间戳 */
            if (s_alarm_start_tick == 0) {
                s_alarm_start_tick = xTaskGetTickCount();
            }

            if (cat_a) {
                /* A 类触发 → 至少 L2 */
                if (cat_a_count >= 2) {
                    level = ALARM_EMERGENCY;    /* L3: 多个 A 类源同时触发 */
                } else {
                    level = ALARM_SEVERE;       /* L2: 单个 A 类源触发 */
                }
            } else {
                level = ALARM_WARNING;          /* L1: 仅 B 类源触发 */
            }

            /* 持续超时 → 强制升级到 L3 */
            TickType_t elapsed = xTaskGetTickCount() - s_alarm_start_tick;
            if (elapsed >= pdMS_TO_TICKS(ALARM_ESCALATE_MS)) {
                level = ALARM_EMERGENCY;
            }
        } else {
            /* 无报警: 重置计时器 */
            s_alarm_start_tick = 0;
        }

        /* 级别变化时打日志 */
        if (level != s_prev_level) {
            ESP_LOGI(TAG, "报警级别变更: %d → %d (cat_a=%d/%d, cat_b=%d)",
                     s_prev_level, level, cat_a, cat_a_count, cat_b);
            s_prev_level = level;
        }

        /* ---- 4. 执行联动控制 ---- */
        int fan_on = cat_a;  /* 风扇仅由 A 类源控制 */

        switch (level) {
        case ALARM_EMERGENCY:   /* L3 紧急 */
            buzzer_set(1);                      /* 长鸣不休 */
            led_set_red(1);                     /* 红灯常亮 */
            led_set_green(0);
            relay_set(fan_on ? 1 : 0);          /* A 类触发 → 开风扇 */
            ESP_LOGW(TAG, "🔴 L3 紧急! 多重危险, 立即排风");
            break;

        case ALARM_SEVERE:      /* L2 严重 */
            buzzer_set(1);                      /* 鸣叫 */
            led_set_red(1);                     /* 红灯亮 */
            led_set_green(0);
            relay_set(1);                       /* A 类触发, 开风扇排风 */
            vTaskDelay(pdMS_TO_TICKS(300));
            buzzer_set(0);                      /* 静音 */
            led_set_red(0);                     /* 红灯灭 (间歇) */
            vTaskDelay(pdMS_TO_TICKS(300));
            ESP_LOGI(TAG, "L2 严重: 毒气/高温, 排风降浓度");
            break;  /* 跳过统一 delay */

        case ALARM_WARNING:     /* L1 预警 */
            buzzer_set(1);                      /* 鸣叫 */
            led_set_red(1);                     /* 红灯亮 */
            led_set_green(0);
            relay_set(0);                       /* B 类源, 风扇不动 */
            vTaskDelay(pdMS_TO_TICKS(200));
            buzzer_set(0);                      /* 静音 */
            vTaskDelay(pdMS_TO_TICKS(800));
            ESP_LOGI(TAG, "L1 预警: 光照/湿度异常, 提醒管理员");
            break;  /* 跳过统一 delay */

        case ALARM_OFF:         /* L0 正常 */
        default:
            buzzer_set(0);                      /* 静音 */
            led_set_red(0);                     /* 红灯灭 */
            led_set_green(1);                   /* 绿灯亮 */
            relay_set(0);                       /* 正常关风扇, 节能 */
            break;
        }

        /* ---- 5. 写回报警状态到共享数据 ---- */
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.buzzer_on   = (level > ALARM_OFF) ? 1 : 0;
            g_sensor_data.alarm_level = level;
            g_sensor_data.fan_on      = fan_on;
            xSemaphoreGive(g_sensor_mutex);
        }

        /* L0/L3 没有内部 vTaskDelay, 统一在此等待 */
        if (level == ALARM_OFF || level == ALARM_EMERGENCY) {
            int delay_ms = (level == ALARM_OFF) ? 500 : 200;  /* L0 慢查, L3 快查 */
            vTaskDelay(pdMS_TO_TICKS(delay_ms));
        }
    }
}

/* ================== UDP 仿真命令接收任务 ================== */
/**
 * @brief 监听 8081 端口，接收 PC GUI 发送的传感器仿真值注入命令
 *
 * JSON 命令格式:
 *   {"dht11_t":39,"dht11_h":60,"ds18b20_t":38.5,"mq135_v":2.8,"mq135_do":0,"photo_raw":2000,"photo_do":1}
 *   {"cmd":"reset"}
 *
 * Apply: 写入 sim_xxx 字段 + sim_active=1
 * Reset: sim_active=0, 恢复真实传感器
 */
static void udp_sim_command_task(void *arg)
{
    (void)arg;

    /* 等待 Wi-Fi 连接 (轮询 esp_netif, 最多 20s, 参考 udp_sender_task) */
    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip_info;
    int wait = 0;
    while (wait < 200) {
        if (sta_netif && esp_netif_is_netif_up(sta_netif)) {
            esp_netif_get_ip_info(sta_netif, &ip_info);
            if (ip_info.ip.addr != 0) break;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
        wait++;
    }
    if (wait >= 200) {
        ESP_LOGE(TAG, "仿真命令: Wi-Fi 连接超时 (20s)");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "仿真命令: Wi-Fi 就绪 (等待约 %dms)", wait * 100);

    /* 创建 UDP socket */
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        ESP_LOGE(TAG, "仿真命令 socket 创建失败, errno=%d", errno);
        vTaskDelete(NULL);
        return;
    }

    /* 绑定到 0.0.0.0:SIM_UDP_PORT */
    struct sockaddr_in local_addr = {0};
    local_addr.sin_family = AF_INET;
    local_addr.sin_port   = htons(SIM_UDP_PORT);
    local_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (bind(sock, (struct sockaddr *)&local_addr, sizeof(local_addr)) < 0) {
        ESP_LOGE(TAG, "仿真命令 bind 失败, errno=%d", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "仿真命令监听就绪, 端口: %d", SIM_UDP_PORT);

    char buf[512];
    struct sockaddr_in src_addr;
    socklen_t addr_len = sizeof(src_addr);

    while (1) {
        memset(buf, 0, sizeof(buf));
        int len = recvfrom(sock, buf, sizeof(buf) - 1, 0,
                          (struct sockaddr *)&src_addr, &addr_len);
        if (len < 0) {
            ESP_LOGW(TAG, "仿真 recvfrom 失败: errno=%d", errno);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        /* ---- 解析 "reset" 命令 ---- */
        if (strstr(buf, "\"reset\"")) {
            /* 切换到真实传感器模式 */
            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                g_sensor_data.sim_active = 0;
                xSemaphoreGive(g_sensor_mutex);
            }
            ESP_LOGI(TAG, "仿真模式已关闭, 恢复真实传感器");
            /* 回复确认 */
            const char *reset_ack = "{\"status\":\"ok\",\"mode\":\"real\"}";
            sendto(sock, reset_ack, strlen(reset_ack), 0,
                  (const struct sockaddr *)&src_addr, addr_len);
            continue;
        }

        /* ---- 解析 "config" 命令: 报警配置 ---- */
        if (strstr(buf, "\"cmd\": \"config\"") || strstr(buf, "\"cmd\":\"config\"")) {
            char *p;
            int   tmp_i = 0;
            float tmp_f = 0.0f;

            if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                /* 字段可选，提取到才更新
                   offset = strlen(needle) 跳过整个 "\"key\":" 来到值首字符 */
                if ((p = strstr(buf, "\"mq135_alarm_src\":")))
                    { if (sscanf(p + 18, "%d", &tmp_i) == 1) g_sensor_data.mq135_alarm_src = (alarm_source_t)tmp_i; }
                if ((p = strstr(buf, "\"photo_alarm_src\":")))
                    { if (sscanf(p + 18, "%d", &tmp_i) == 1) g_sensor_data.photo_alarm_src = (alarm_source_t)tmp_i; }
                if ((p = strstr(buf, "\"mq135_ao_dir\":")))
                    { if (sscanf(p + 15, "%d", &tmp_i) == 1) g_sensor_data.mq135_ao_dir = (ao_trigger_dir_t)tmp_i; }
                if ((p = strstr(buf, "\"photo_ao_dir\":")))
                    { if (sscanf(p + 15, "%d", &tmp_i) == 1) g_sensor_data.photo_ao_dir = (ao_trigger_dir_t)tmp_i; }
                if ((p = strstr(buf, "\"mq135_ao_threshold\":")))
                    { if (sscanf(p + 21, "%f", &tmp_f) == 1) g_sensor_data.mq135_ao_threshold = tmp_f; }
                if ((p = strstr(buf, "\"photo_ao_threshold\":")))
                    { if (sscanf(p + 21, "%d", &tmp_i) == 1) g_sensor_data.photo_ao_threshold = tmp_i; }
                if ((p = strstr(buf, "\"dht11_temp_high\":")))
                    { if (sscanf(p + 18, "%d", &tmp_i) == 1) g_sensor_data.dht11_temp_high = tmp_i; }
                if ((p = strstr(buf, "\"dht11_humi_high\":")))
                    { if (sscanf(p + 18, "%d", &tmp_i) == 1) g_sensor_data.dht11_humi_high = tmp_i; }
                if ((p = strstr(buf, "\"ds18b20_temp_high\":")))
                    { if (sscanf(p + 20, "%f", &tmp_f) == 1) g_sensor_data.ds18b20_temp_high = tmp_f; }
                if ((p = strstr(buf, "\"temp_humi_alarm_enabled\":")))
                    { if (sscanf(p + 26, "%d", &tmp_i) == 1) g_sensor_data.temp_humi_alarm_enabled = tmp_i; }
                xSemaphoreGive(g_sensor_mutex);
            }

            /* 异步持久化到 NVS */
            save_alarm_config_to_nvs();

            ESP_LOGI(TAG, "报警配置已更新: mq_src=%d mq_thr=%.2fV ph_src=%d ph_thr=%d dht_t=%d dht_h=%d ds_t=%.1f temp_en=%d",
                     g_sensor_data.mq135_alarm_src, g_sensor_data.mq135_ao_threshold,
                     g_sensor_data.photo_alarm_src, g_sensor_data.photo_ao_threshold,
                     g_sensor_data.dht11_temp_high, g_sensor_data.dht11_humi_high,
                     g_sensor_data.ds18b20_temp_high, g_sensor_data.temp_humi_alarm_enabled);

            /* 回复确认 */
            const char *cfg_ack = "{\"status\":\"ok\",\"cmd\":\"config\"}";
            sendto(sock, cfg_ack, strlen(cfg_ack), 0,
                  (const struct sockaddr *)&src_addr, addr_len);
            continue;
        }

        /* ---- 解析注入命令: 提取各字段 ---- */
        int sim_dht11_t   = 25, sim_dht11_h = 60;
        float sim_ds18b20 = 25.0f;
        int sim_mq135_do  = 1, sim_photo_do = 1, sim_photo_raw = 2000;
        float sim_mq135_v = 1.2f;
        int parsed = 0;

        /* 使用 sscanf 提取各字段 (字段可选, 提取不到保持默认值) */
        if (sscanf(buf, "%*[^0-9]%d", &sim_dht11_t) < 0) {} /* dummy */
        {
            char *p;
            if ((p = strstr(buf, "\"dht11_t\":")  )) sscanf(p+10, "%d",   &sim_dht11_t),  parsed++;
            if ((p = strstr(buf, "\"dht11_h\":")  )) sscanf(p+10, "%d",   &sim_dht11_h),  parsed++;
            if ((p = strstr(buf, "\"ds18b20_t\":"))) sscanf(p+12, "%f",   &sim_ds18b20),  parsed++;
            if ((p = strstr(buf, "\"mq135_v\":")  )) sscanf(p+10, "%f",   &sim_mq135_v),  parsed++;
            if ((p = strstr(buf, "\"mq135_do\":") )) sscanf(p+10, "%d",   &sim_mq135_do), parsed++;
            if ((p = strstr(buf, "\"photo_raw\":"))) sscanf(p+12, "%d",   &sim_photo_raw),parsed++;
            if ((p = strstr(buf, "\"photo_do\":") )) sscanf(p+10, "%d",   &sim_photo_do), parsed++;
        }

        if (parsed == 0) {
            ESP_LOGW(TAG, "仿真命令无法解析: %s", buf);
            continue;
        }

        /* 写入仿真值 + 开启仿真模式 */
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.sim_dht11_t   = sim_dht11_t;
            g_sensor_data.sim_dht11_h   = sim_dht11_h;
            g_sensor_data.sim_ds18b20_t = sim_ds18b20;
            g_sensor_data.sim_mq135_v   = sim_mq135_v;
            g_sensor_data.sim_mq135_do  = sim_mq135_do;
            g_sensor_data.sim_photo_raw = sim_photo_raw;
            g_sensor_data.sim_photo_do  = sim_photo_do;
            g_sensor_data.sim_active    = 1;
            xSemaphoreGive(g_sensor_mutex);
        }

        ESP_LOGI(TAG, "仿真注入 [SIM]: Tdht=%d H=%d%% Tds=%.1f MQv=%.2f MQdo=%d PR=%d Pdo=%d",
                 sim_dht11_t, sim_dht11_h, sim_ds18b20,
                 sim_mq135_v, sim_mq135_do, sim_photo_raw, sim_photo_do);

        /* 回复确认 */
        char ack[128];
        snprintf(ack, sizeof(ack),
            "{\"status\":\"ok\",\"mode\":\"sim\",\"dht11_t\":%d,\"dht11_h\":%d,"
            "\"ds18b20_t\":%.1f,\"mq135_v\":%.2f,\"mq135_do\":%d,"
            "\"photo_raw\":%d,\"photo_do\":%d}",
            sim_dht11_t, sim_dht11_h, sim_ds18b20,
            sim_mq135_v, sim_mq135_do, sim_photo_raw, sim_photo_do);
        sendto(sock, ack, strlen(ack), 0,
              (const struct sockaddr *)&src_addr, addr_len);
    }

    close(sock);
    vTaskDelete(NULL);
}

/* ================== 主入口 ================== */
void app_main(void)
{
    ESP_LOGI(TAG, "ESP32-P4 智能环境监测系统 + OLED 显示");

    /* 创建互斥锁 */
    g_sensor_mutex = xSemaphoreCreateMutex();
    if (g_sensor_mutex == NULL) {
        ESP_LOGE(TAG, "❌ 互斥锁创建失败! 系统无法启动");
        while (1) { vTaskDelay(pdMS_TO_TICKS(5000)); }  /* 持续报错, 避免静默假死 */
        return;
    }

    /* 初始化 OLED */
    esp_err_t ret = oled_init();
    bool oled_ok = (ret == ESP_OK);
    if (!oled_ok) {
        ESP_LOGE(TAG, "OLED 初始化失败: %s, 跳过显示任务", esp_err_to_name(ret));
    }

    /* WiFi STA 初始化 — 当前注释，后续需要时启用 */
    wifi_init_sta();

    /* 从 NVS 加载报警配置（必须在 buzzer_task 创建前完成） */
    load_alarm_config_from_nvs();

    /* 创建 MQ-135 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(mq135_task, "mq135_sensor", 4096, NULL, 3, NULL);

    /* 创建 DS18B20 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(ds18b20_task, "ds18b20_sensor", 4096, NULL, 3, NULL);

    /* 创建 DHT11 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(dht11_task, "dht11_sensor", 4096, NULL, 3, NULL);

    /* 创建光敏电阻传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(photo_sensor_task, "photo_sensor", 4096, NULL, 3, NULL);

    /* 创建 OLED 显示刷新任务 (优先级2, 栈4096) — 仅在初始化成功时创建 */
    if (oled_ok) {
        xTaskCreate(oled_display_task, "oled_display", 4096, NULL, 2, NULL);
    }

    /* 创建蜂鸣器报警任务 (优先级2, 栈5120 — 局部变量多+ESP_LOG格式化) */
    xTaskCreate(buzzer_task, "buzzer_alarm", 5120, NULL, 2, NULL);

    /* 创建 UDP 传感器数据发送任务 (优先级2, 栈5120, 不绑核避免 SDIO 中断冲突) */
    xTaskCreate(udp_sender_task, "Task_UDP_Send", 5120, NULL, 2, NULL);

    /* 创建 UDP 仿真命令接收任务 (优先级1, 栈4096) — 局域网 PC GUI 直接注入 */
    xTaskCreate(udp_sim_command_task, "Task_UDP_Sim", 4096, NULL, 1, NULL);

    /* 创建 HTTP 仿真命令轮询任务 (优先级1, 栈8192) — 公网 VPS 反转轮询 */
    xTaskCreate(sim_poll_task, "Task_Sim_Poll", 8192, NULL, 1, NULL);
}
