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

/* ================== MQ-135 空气质量传感器读取任务 ================== */
static void mq135_task(void *arg)
{
    mq135_data_t data;

    esp_err_t ret = mq135_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "MQ-135 初始化失败");
        vTaskDelete(NULL);
        return;
    }

    while (1) {
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

    esp_err_t ret = ds18b20_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "DS18B20 初始化失败");
        vTaskDelete(NULL);
        return;
    }

    while (1) {
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

    esp_err_t ret = dht11_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "DHT11 初始化失败");
        vTaskDelete(NULL);
        return;
    }

    while (1) {
        dht11_read(&data);

        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            g_sensor_data.dht11_temp = data.temp;
            g_sensor_data.dht11_humi = data.humi;
            g_sensor_data.dht11_err  = data.err;
            xSemaphoreGive(g_sensor_mutex);
        }

        if (data.err) {
            ESP_LOGW(TAG, "DHT11: 读取失败, err=0x%02X", data.err);
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

    esp_err_t ret = photo_sensor_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "光敏电阻传感器初始化失败");
        vTaskDelete(NULL);
        return;
    }

    while (1) {
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
            oled_show_line(3, "MQ135: %.2fV %s",
                           local.mq135_voltage,
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

        /* ---- 行 5: 报警状态汇总 ---- */
        /* 预热期间忽略 MQ-135 DO，避免误统计 (与 buzzer_task 逻辑一致) */
        int mq135_do_for_alert = mq135_is_warmed_up() ? local.mq135_do : 1;
        int alert = (!mq135_do_for_alert || !local.photo_do) ? 1 : 0;
        int err_sum = local.dht11_err | local.ds18b20_err | local.mq135_err | local.photo_err;
        oled_show_line(5, "Alrt:%s Err:0x%02x",
                       alert ? "ON " : "OFF", err_sum);

        /* ---- 行 6: 蜂鸣器 + Wi-Fi 状态 ---- */
        oled_show_line(6, "Buz:%s  WiFi:%s",
                       local.buzzer_on ? "ON " : "OFF",
                       local.wifi_connected ? "OK  " : "DOWN");

        /* ---- 行 7: 继电器+LED 状态 ---- */
        int relay_on = !local.buzzer_on;  /* 正常时继电器闭合(风扇启动), 报警时断开 */
        oled_show_line(7, "Fan:%s LED:%s",
                       relay_on ? "ON " : "OFF",
                       relay_on ? "GRN" : "RED");

        vTaskDelay(pdMS_TO_TICKS(1000));  /* 每 1 秒刷新一次 */
    }
}

/* ================== 蜂鸣器报警控制任务 ================== */
static void buzzer_task(void *arg)
{
    esp_err_t ret = buzzer_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "蜂鸣器初始化失败");
        vTaskDelete(NULL);
        return;
    }

    ret = led_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "LED初始化失败");
    }

    ret = relay_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "继电器初始化失败");
    }

    while (1) {
        int mq135_do = 1;    /* 默认正常 (预热期间强制为1) */
        int photo_do = 1;

        /* 持锁读取报警状态 */
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            /* 预热期间忽略 MQ-135 DO，避免误报警 (REQUIREMENT.md 4.3: 预热≥3分钟) */
            mq135_do  = mq135_is_warmed_up() ? g_sensor_data.mq135_do : 1;
            photo_do  = g_sensor_data.photo_do;

            /* 更新蜂鸣器状态到共享数据 (供 OLED 显示) */
            g_sensor_data.buzzer_on = (!mq135_do || !photo_do) ? 1 : 0;
            xSemaphoreGive(g_sensor_mutex);
        }

        /* 报警逻辑: MQ-135 或 光敏任一 DO=0 → 触发报警 */
        if (!mq135_do || !photo_do) {
            /* 报警状态: 蜂鸣器鸣叫, 红灯亮, 绿灯灭, 继电器闭合(风扇启动) */
            buzzer_set(1);                          /* 鸣叫 */
            led_set_red(1);                         /* 红灯亮 */
            led_set_green(0);                       /* 绿灯灭 */
            relay_set(0);                           /* 继电器断开(风扇停止) */
            vTaskDelay(pdMS_TO_TICKS(100));
            /* 间歇停止: 蜂鸣器静音, LED保持报警状态, 继电器保持断开 */
            buzzer_set(0);                          /* 静音 */
            vTaskDelay(pdMS_TO_TICKS(500));
        } else {
            /* 正常状态: 红灯灭, 绿灯亮, 继电器闭合(风扇启动) */
            led_set_red(0);                         /* 红灯灭 */
            led_set_green(1);                       /* 绿灯亮 */
            relay_set(1);                           /* 继电器闭合(风扇启动) */
            vTaskDelay(pdMS_TO_TICKS(500));         /* 正常时每 500ms 检查一次 */
        }
    }
}

/* ================== 主入口 ================== */
void app_main(void)
{
    ESP_LOGI(TAG, "ESP32-P4 智能环境监测系统 + OLED 显示");

    /* 创建互斥锁 */
    g_sensor_mutex = xSemaphoreCreateMutex();
    if (g_sensor_mutex == NULL) {
        ESP_LOGE(TAG, "互斥锁创建失败!");
        return;
    }

    /* 初始化 OLED */
    esp_err_t ret = oled_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "OLED 初始化失败: %s", esp_err_to_name(ret));
    }

    /* WiFi STA 初始化 — 当前注释，后续需要时启用 */
    wifi_init_sta();

    /* 创建 MQ-135 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(mq135_task, "mq135_sensor", 4096, NULL, 3, NULL);

    /* 创建 DS18B20 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(ds18b20_task, "ds18b20_sensor", 4096, NULL, 3, NULL);

    /* 创建 DHT11 传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(dht11_task, "dht11_sensor", 4096, NULL, 3, NULL);

    /* 创建光敏电阻传感器读取任务 (优先级3, 栈4096) */
    xTaskCreate(photo_sensor_task, "photo_sensor", 4096, NULL, 3, NULL);

    /* 创建 OLED 显示刷新任务 (优先级2, 栈4096) */
    xTaskCreate(oled_display_task, "oled_display", 4096, NULL, 2, NULL);

    /* 创建蜂鸣器报警任务 (优先级2, 栈2048) */
    xTaskCreate(buzzer_task, "buzzer_alarm", 2048, NULL, 2, NULL);

    /* 创建 UDP 传感器数据发送任务 (优先级2, 栈4096, 绑核1) (REQUIREMENT.md 5.2) */
    xTaskCreatePinnedToCore(udp_sender_task, "Task_UDP_Send", 4096, NULL, 2, NULL, 1);
}
