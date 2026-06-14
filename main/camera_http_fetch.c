/**
 * @file camera_http_fetch.c
 * @brief HTTP 拉取 ESP32-CAM JPEG → UDP 分包转发实现
 *
 * 依赖: esp_http_client (IDF 内置, 无需额外 component)
 *
 * 配置 (Kconfig):
 *   - CAMERA_HTTP_ESP32CAM_URL: ESP32-CAM 的 /capture 地址
 *   - CAMERA_HTTP_UDP_IP:      PC 端 IP
 *   - CAMERA_HTTP_FPS:         拉图帧率 (1-10)
 */

#include "camera_http_fetch.h"
#include "sdkconfig.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_http_client.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include <string.h>
#include <stdlib.h>

static const char *TAG = "cam_http";

/* ==================== 配置（来自 Kconfig, 带默认值回退）==================== */
#ifndef CONFIG_CAMERA_HTTP_ESP32CAM_URL
#define CONFIG_CAMERA_HTTP_ESP32CAM_URL "http://10.16.234.23/capture"
#endif
/* 强制覆盖 Kconfig 默认值 (sdkconfig 可能缓存旧值) */
#undef CONFIG_CAMERA_HTTP_UDP_IP
#define CONFIG_CAMERA_HTTP_UDP_IP "38.55.199.220"
#undef CONFIG_CAMERA_HTTP_FPS
#define CONFIG_CAMERA_HTTP_FPS 10

#define CAM_URL          CONFIG_CAMERA_HTTP_ESP32CAM_URL
#define CAM_UDP_IP       CONFIG_CAMERA_HTTP_UDP_IP
#define CAM_UDP_PORT     8003
#define CAM_FPS          CONFIG_CAMERA_HTTP_FPS
#define CAM_FRAME_MS     (1000 / CAM_FPS)

/* ==================== 缓冲区大小 ==================== */
#define JPEG_BUF_SIZE    (128 * 1024)   /* 128KB 足够 640x480 JPEG */
#define UDP_PKT_BUF_SIZE 65536          /* 64KB UDP 拼包缓冲 */
#define UDP_PAYLOAD_MAX  4096           /* 单包载荷上限 (兼容 camera_protocol.py) */

/* ==================== 协议常量 (与 camera_protocol.py 一致) ==================== */
#define PROTO_HEADER_SIZE 8
#define PROTO_MAGIC       0xAA55

/**
 * @brief 编码 8 字节协议头 (大端序)
 */
static void proto_pack_header(uint8_t *buf, uint16_t frame_id,
                               uint16_t chunk_idx, uint16_t total_chunks)
{
    buf[0] = 0xAA;
    buf[1] = 0x55;
    buf[2] = (frame_id >> 8) & 0xFF;
    buf[3] = frame_id & 0xFF;
    buf[4] = (chunk_idx >> 8) & 0xFF;
    buf[5] = chunk_idx & 0xFF;
    buf[6] = (total_chunks >> 8) & 0xFF;
    buf[7] = total_chunks & 0xFF;
}

void camera_http_fetch_task(void *arg)
{
    (void)arg;

    /* ===== 1. 轮询等待 Wi-Fi 获取 IP (ESP-Hosted SDIO 需要 ~13s) ===== */
    ESP_LOGI(TAG, "等待 Wi-Fi 连接...");
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
            ESP_LOGE(TAG, "Wi-Fi 连接超时, 退出");
            vTaskDelete(NULL);
            return;
        }
        ESP_LOGI(TAG, "Wi-Fi 就绪 (等待 %d ms)", wait * 100);
    }

    /* ===== 2. 创建 UDP Socket ===== */
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        ESP_LOGE(TAG, "UDP socket 创建失败, errno=%d", errno);
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in dest_addr = {0};
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port   = htons(CAM_UDP_PORT);
    if (inet_aton(CAM_UDP_IP, &dest_addr.sin_addr) == 0) {
        ESP_LOGE(TAG, "无效目标 IP: %s", CAM_UDP_IP);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "ESP32-CAM: %s | UDP → %s:%d @ %d fps",
             CAM_URL, CAM_UDP_IP, CAM_UDP_PORT, CAM_FPS);

    /* ===== 3. 分配缓冲 (PSRAM, 32MB 充足) ===== */
    uint8_t *jpeg_buf = malloc(JPEG_BUF_SIZE);
    uint8_t *udp_pkt  = malloc(UDP_PKT_BUF_SIZE);
    if (!jpeg_buf || !udp_pkt) {
        ESP_LOGE(TAG, "缓冲分配失败 (JPEG=%p UDP=%p)",
                 (void *)jpeg_buf, (void *)udp_pkt);
        free(jpeg_buf);
        free(udp_pkt);
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "缓冲就绪: JPEG=%uKB UDP=%uKB",
             JPEG_BUF_SIZE / 1024, UDP_PKT_BUF_SIZE / 1024);

    /* ===== 4. HTTP 客户端配置 (全局复用, 启用 keep-alive 长连接) ===== */
    esp_http_client_config_t http_cfg = {
        .url               = CAM_URL,
        .method            = HTTP_METHOD_GET,
        .timeout_ms        = 10000,
        .keep_alive_enable = true,
    };

    uint16_t frame_id = 0;

    /* ===== 5. HTTP 客户端创建 (循环外 init 一次, 长连接复用) ===== */
    esp_http_client_handle_t client = esp_http_client_init(&http_cfg);
    if (!client) {
        ESP_LOGE(TAG, "HTTP client init 失败");
        free(udp_pkt);
        free(jpeg_buf);
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "HTTP 长连接就绪 (keep_alive)");

    /* ===== 6. 主循环: HTTP GET → 读 JPEG → UDP 分包发送 ===== */
    int http_retry_count = 0;
    const int HTTP_MAX_RETRIES = 5;  // ★ 最大重连次数，防止无限循环
    while (1) {
        TickType_t loop_start = xTaskGetTickCount();

        /* ---- 6.0 守护: 确保 client 非空（防止 re-init 失败后 NULL 解引用）---- */
        if (!client) {
            ESP_LOGE(TAG, "HTTP client 为 NULL, 尝试重新初始化...");
            client = esp_http_client_init(&http_cfg);
            if (!client) {
                ESP_LOGE(TAG, "HTTP re-init 持续失败, 3s 后重试");
                vTaskDelay(pdMS_TO_TICKS(3000));
                continue;
            }
        }

        /* ---- 6.1 发起 HTTP GET 请求 ---- */
        esp_err_t err = esp_http_client_open(client, 0);  /* write_len=0 → GET */
        if (err != ESP_OK) {
            http_retry_count++;
            ESP_LOGE(TAG, "HTTP open 失败 (%d/%d): %s (%d), 重连...",
                     http_retry_count, HTTP_MAX_RETRIES, esp_err_to_name(err), err);
            esp_http_client_cleanup(client);
            client = NULL;  // ★ 先置 NULL，让 6.0 守护段在下轮重新 init
            if (http_retry_count > HTTP_MAX_RETRIES) {
                ESP_LOGE(TAG, "HTTP 重连超过上限 %d 次, 10s 冷却后重置计数",
                         HTTP_MAX_RETRIES);
                vTaskDelay(pdMS_TO_TICKS(10000));
                http_retry_count = 0;
            } else {
                vTaskDelay(pdMS_TO_TICKS(500));
            }
            continue;
        }
        http_retry_count = 0;  // ★ 成功后重置计数

        int content_length = esp_http_client_fetch_headers(client);
        int status = esp_http_client_get_status_code(client);

        if (status != 200 || content_length <= 0) {
            ESP_LOGW(TAG, "HTTP %d, Content-Length=%d", status, content_length);
            esp_http_client_close(client);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        /* ---- 6.2 读取 JPEG 数据 ---- */
        int total_read = 0;
        int read_len = 0;

        while (total_read < JPEG_BUF_SIZE &&
               (read_len = esp_http_client_read(client,
                        (char *)(jpeg_buf + total_read),
                        JPEG_BUF_SIZE - total_read)) > 0) {
            total_read += read_len;
        }

        esp_http_client_close(client);

        if (read_len < 0) {
            ESP_LOGW(TAG, "HTTP read 错误: errno=%d (已读 %d 字节), 丢弃不完整帧",
                     errno, total_read);
            vTaskDelay(pdMS_TO_TICKS(100));  // ★ 跳过发送，防止畸形 JPEG 传输
            continue;
        }

        if (total_read <= 0) {
            ESP_LOGW(TAG, "未收到 JPEG 数据 (status=%d)", status);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        ESP_LOGI(TAG, "HTTP read 完成 (%d 字节)", total_read);

        /* ---- 6.3 UDP 分包发送 ---- */
        uint16_t total_chunks = (uint16_t)((total_read + UDP_PAYLOAD_MAX - 1)
                                           / UDP_PAYLOAD_MAX);

        for (uint16_t chunk = 0; chunk < total_chunks; chunk++) {
            uint32_t offset = (uint32_t)chunk * UDP_PAYLOAD_MAX;
            uint32_t remain = (uint32_t)total_read - offset;
            uint32_t chunk_len = (remain > UDP_PAYLOAD_MAX)
                                 ? UDP_PAYLOAD_MAX : remain;

            proto_pack_header(udp_pkt, frame_id, chunk, total_chunks);
            memcpy(udp_pkt + PROTO_HEADER_SIZE, jpeg_buf + offset, chunk_len);

            int sent = sendto(sock, udp_pkt,
                             (int)(PROTO_HEADER_SIZE + chunk_len),
                             0,
                             (const struct sockaddr *)&dest_addr,
                             sizeof(dest_addr));
            if (sent < 0) {
                ESP_LOGW(TAG, "UDP sendto 失败: errno=%d (包 %u/%u)",
                         errno, chunk + 1, total_chunks);
            }

            /* 1 tick 延迟让 WiFi 栈完成前一个包发送, 避免 burst 丢包 */
            vTaskDelay(1);
        }

        /* ---- 6.4 进度日志 (每 30 帧打印一次) ---- */
        if (frame_id % 30 == 0) {
            ESP_LOGI(TAG, "#%04u | JPEG:%dB → %u 包 | HTTP %dms",
                     frame_id, total_read, total_chunks,
                     (int)pdTICKS_TO_MS(xTaskGetTickCount() - loop_start));
        }

        frame_id++;

        /* ---- 6.5 帧率控制 ---- */
        TickType_t elapsed = xTaskGetTickCount() - loop_start;
        int32_t remain_ms = CAM_FRAME_MS - (int32_t)pdTICKS_TO_MS(elapsed);
        if (remain_ms > 0) {
            vTaskDelay(pdMS_TO_TICKS(remain_ms));
        }
    }

    /* 不会执行到这里, 但保持防御性清理 */
    esp_http_client_cleanup(client);
    free(udp_pkt);
    free(jpeg_buf);
    close(sock);
    vTaskDelete(NULL);
}
