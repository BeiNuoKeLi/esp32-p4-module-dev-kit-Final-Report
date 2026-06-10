/**
 * @file udp_sender.c
 * @brief UDP 传感器数据发送任务实现
 *
 * 通信协议（REQUIREMENT.md 6.1）：
 *   - 目标 IP: 192.168.5.5
 *   - 目标端口: 8080
 *   - 传输方式: UDP (SOCK_DGRAM)
 *   - 发送间隔: 每 2 秒
 *   - 单包最大: < 512 字节
 *
 * JSON 报文格式 v2.0（分级报警）：
 *   {"type":"data","level":0,"ts":毫秒,"dht11_t":°C,"dht11_h":%,
 *    "ds18b20_t":°C,"mq135_v":V,"light_v":V,"alert":0/1,"err":位掩码,
 *    "reason":"触发原因(mq135/dht11_temp/ds18b20_temp/photo/dht11_humi)"}
 *
 * 组包方式（REQUIREMENT.md 6.3）：
 *   - 使用 snprintf() 直接拼接，不引入 cJSON 等第三方库
 *
 * Socket API: lwip/sockets.h 提供的 BSD socket API
 *   - socket(AF_INET, SOCK_DGRAM, 0) → 创建 UDP socket
 *   - sendto() → 发送数据报
 *   - close() → 关闭 socket
 *
 * 时间戳（REQUIREMENT.md 6.2）：
 *   - ts: FreeRTOS 启动后毫秒时间戳
 *   - 来源: esp_timer_get_time() / 1000
 */

#include "udp_sender.h"
#include "sensors.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "lwip/sockets.h"
#include <string.h>
#include <stdio.h>

static const char *TAG_UDP = "udp_sender";

/* ==================== UDP 目标地址（REQUIREMENT.md 6.1）==================== */
#define UDP_TARGET_IP     "10.16.234.215"  /*!< 上位机 IP (2026-06-04 通过 ipconfig 确认) */
#define UDP_TARGET_PORT   8080           /*!< 通信端口 */
#define UDP_SEND_INTERVAL 2000           /*!< 发送间隔, 毫秒（REQUIREMENT.md 6.1: 每2秒） */
#define UDP_BUF_SIZE      512            /*!< 发送缓冲区（REQUIREMENT.md 6.1: 单包 < 512 字节） */

void udp_sender_task(void *arg)
{
    (void)arg;

    /* ---- 1. 轮询等待 Wi-Fi 获取 IP (ESP-Hosted SDIO 需要 ~13s) ---- */
    ESP_LOGI(TAG_UDP, "等待 Wi-Fi 连接...");
    {
        esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        esp_netif_ip_info_t ip_info;
        int wait = 0;
        while (wait < 200) {  /* 最多等 20s */
            if (sta_netif && esp_netif_is_netif_up(sta_netif)) {
                esp_netif_get_ip_info(sta_netif, &ip_info);
                if (ip_info.ip.addr != 0) break;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            wait++;
        }
        if (wait >= 200) {
            ESP_LOGE(TAG_UDP, "Wi-Fi 连接超时");
            vTaskDelete(NULL);
            return;
        }
    }

    /* ---- 2. 创建 UDP socket ---- */
    /* API: socket(domain, type, protocol)
     * 来自 lwip/sockets.h，兼容 POSIX */
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        ESP_LOGE(TAG_UDP, "创建 UDP socket 失败, errno=%d", errno);
        vTaskDelete(NULL);
        return;
    }

    /* ---- 3. 配置目标地址 ---- */
    /* struct sockaddr_in 来自 lwip/sockets.h (lwIP 的 BSD socket 兼容层)
     * inet_aton() 将点分十进制 IP 转为 32 位网络字节序 */
    struct sockaddr_in dest_addr = {0};
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port   = htons(UDP_TARGET_PORT);
    if (inet_aton(UDP_TARGET_IP, &dest_addr.sin_addr) == 0) {
        ESP_LOGE(TAG_UDP, "无效的目标 IP 地址: %s", UDP_TARGET_IP);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG_UDP, "UDP 发送器就绪, 目标: %s:%d", UDP_TARGET_IP, UDP_TARGET_PORT);

    /* ---- 4. 主循环: 读取传感器 → 构建 JSON → 发送 ---- */
    char buf[UDP_BUF_SIZE];
    sensor_shared_t local;

    while (1) {
        /* ---- 4.1 持锁读取共享传感器数据（REQUIREMENT.md 5.3）---- */
        memset(&local, 0, sizeof(local));
        if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
            memcpy(&local, &g_sensor_data, sizeof(sensor_shared_t));
            xSemaphoreGive(g_sensor_mutex);
        }

        /* ---- 4.2 计算派生数据 ---- */

        /* 时间戳: 毫秒 (REQUIREMENT.md 6.2: FreeRTOS 启动后时间戳)
         * esp_timer_get_time() 返回从 boot 开始的微秒数 */
        unsigned long ts = (unsigned long)(esp_timer_get_time() / 1000);

        /* 光敏电压换算（REQUIREMENT.md 5.4）:
         * voltage = adc_reading * 3.3f / 4095.0f */
        float light_v = (local.photo_raw >= 0)
                        ? local.photo_raw * 3.3f / 4095.0f
                        : -1.0f;

        /* MQ-135 电压: 已在 mq135_read() 中计算并存入 g_sensor_data */
        float mq135_v = local.mq135_voltage;

        /* 报警标志 (v2.0 分级报警):
         * alert = 任一报警源触发 (A 类 OR B 类)
         * A 类: MQ-135 DO=0 / DHT11 高温 / DS18B20 高温
         * B 类: 光敏 DO=0 / DHT11 高湿 */
        int alert_a_mq135    = (!local.mq135_do) ? 1 : 0;
        int alert_a_temp_dht = (local.dht11_temp >= ALARM_TEMP_HIGH_DHT11
                                && local.dht11_temp <= 50) ? 1 : 0;
        int alert_a_temp_ds  = (local.ds18b20_temp >= ALARM_TEMP_HIGH_DS18B20
                                && local.ds18b20_temp <= 125.0f) ? 1 : 0;
        int alert_b_photo    = (!local.photo_do) ? 1 : 0;
        int alert_b_humi     = (local.dht11_humi >= ALARM_HUMI_HIGH
                                && local.dht11_humi <= 90) ? 1 : 0;
        int alert = (alert_a_mq135 || alert_a_temp_dht || alert_a_temp_ds
                     || alert_b_photo || alert_b_humi) ? 1 : 0;

        /* 报警原因: 记录具体哪些传感器触发了报警 (v2.0 扩展) */
        char reason[64] = "";
        if (alert) {
            int first = 1;
            if (alert_a_mq135)    { snprintf(reason + strlen(reason),
                 sizeof(reason) - strlen(reason), "%smq135", first ? "" : ","); first = 0; }
            if (alert_a_temp_dht) { snprintf(reason + strlen(reason),
                 sizeof(reason) - strlen(reason), "%sdht11_temp", first ? "" : ","); first = 0; }
            if (alert_a_temp_ds)  { snprintf(reason + strlen(reason),
                 sizeof(reason) - strlen(reason), "%sds18b20_temp", first ? "" : ","); first = 0; }
            if (alert_b_photo)    { snprintf(reason + strlen(reason),
                 sizeof(reason) - strlen(reason), "%sphoto", first ? "" : ","); first = 0; }
            if (alert_b_humi)     { snprintf(reason + strlen(reason),
                 sizeof(reason) - strlen(reason), "%sdht11_humi", first ? "" : ","); first = 0; }
        }

        /* 错误码（REQUIREMENT.md 5.7 位掩码）:
         * bit0 = DHT11, bit1 = DS18B20, bit2 = MQ135, bit3 = 光敏 */
        int err = local.dht11_err | local.ds18b20_err
                | local.mq135_err  | local.photo_err;

        /* ---- 4.3 构建 JSON 报文（v3.0: 新增 mq135_do + photo_do）---- */
        int written = snprintf(buf, sizeof(buf),
            "{\"type\":\"data\",\"level\":%d,"
            "\"ts\":%lu,\"dht11_t\":%.1f,\"dht11_h\":%.1f,"
            "\"ds18b20_t\":%.4f,\"mq135_v\":%.2f,\"light_v\":%.2f,"
            "\"mq135_do\":%d,\"photo_do\":%d,"
            "\"alert\":%d,\"err\":%d,\"reason\":\"%s\"}",
            (int)local.alarm_level,
            ts,
            (float)local.dht11_temp, (float)local.dht11_humi,
            local.ds18b20_temp, mq135_v, light_v,
            (int)local.mq135_do, (int)local.photo_do,
            alert, err, reason);

        /* 检查是否超出缓冲区（REQUIREMENT.md 6.1: < 512 字节） */
        if (written < 0 || written >= (int)sizeof(buf)) {
            ESP_LOGE(TAG_UDP, "JSON 溢出或格式化错误: written=%d, bufsize=%d",
                     written, (int)sizeof(buf));
            vTaskDelay(pdMS_TO_TICKS(UDP_SEND_INTERVAL));
            continue;
        }

        /* ---- 4.4 发送 UDP 数据报 (带 ENOMEM 退避重试) ---- */
        /* API: sendto(sock, buf, len, flags, dest_addr, addrlen)
         * 来自 lwip/sockets.h
         *
         * errno=12 (ENOMEM): lwIP pbuf 池暂时耗尽 (Camera 大包占用了),
         * 等待 200ms 让 Camera 完成发送后重试, 最多 3 次 */
        int sent = -1;
        for (int retry = 0; retry < 3; retry++) {
            sent = sendto(sock, buf, written, 0,
                          (const struct sockaddr *)&dest_addr,
                          sizeof(dest_addr));
            if (sent >= 0) break;

            if (errno == 12) { /* ENOMEM: pbuf 暂时耗尽, 可恢复 */
                if (retry < 2) {
                    vTaskDelay(pdMS_TO_TICKS(200));
                }
            } else {
                break; /* 其他错误不重试 */
            }
        }

        if (sent < 0) {
            /* 只对非 ENOMEM 错误打 WARNING, ENOMEM 打 DEBUG 避免刷屏 */
            if (errno == 12) {
                ESP_LOGD(TAG_UDP, "UDP 发送 ENOMEM (已重试), 跳过本轮");
            } else {
                ESP_LOGW(TAG_UDP, "UDP 发送失败: errno=%d", errno);
            }
        } else {
            ESP_LOGI(TAG_UDP, "已发送 (%d bytes): %s", sent, buf);
        }

        /* ---- 4.5 等待下一个发送周期（REQUIREMENT.md 6.1: 每2秒）---- */
        vTaskDelay(pdMS_TO_TICKS(UDP_SEND_INTERVAL));
    }

    /* ---- 5. 清理（正常不会到达这里）---- */
    close(sock);
    vTaskDelete(NULL);
}
