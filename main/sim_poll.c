/**
 * @file sim_poll.c
 * @brief 仿真命令 HTTP 轮询实现 (反转通信方向)
 *
 * ESP32 主动 HTTP GET 拉取 VPS 上暂存的仿真命令，替代旧方案中
 * VPS → ESP32 的 UDP 入站（被 NAT 阻挡）。
 *
 * 依赖: esp_http_client (IDF 内置), sensors.h (共享数据)
 */

#include "sim_poll.h"
#include "sensors.h"
#include "sdkconfig.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static const char *TAG = "sim_poll";

/* ==================== 配置（硬编码, 后续可移入 Kconfig）==================== */
#define SIM_POLL_URL    "http://38.55.199.220:8001/api/sim/poll"
#define SIM_POLL_MS     3000   /* 轮询间隔 3s */

/* HTTP 响应缓冲区 — 仿真命令很小 (<512B) */
#define SIM_RESP_BUF    1024

/* ==================== 内部工具函数 ==================== */

/**
 * @brief HTTP GET 请求，返回响应体 (malloc 的字符串, 调用者 free)
 * @return 响应体字符串 或 NULL
 */
static char *http_get_body(const char *url)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = 5000,
        .buffer_size = SIM_RESP_BUF,
        .user_agent = "SmartMonitor/3.0 Poll",
        .disable_auto_redirect = false,
    };

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        ESP_LOGW(TAG, "HTTP 客户端初始化失败");
        return NULL;
    }

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "HTTP open 失败: %d", err);
        esp_http_client_cleanup(client);
        return NULL;
    }

    int content_len = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status != 200 || content_len <= 0) {
        ESP_LOGW(TAG, "HTTP %d | content_len=%d", status, content_len);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return NULL;
    }

    /* 分配缓冲区并读取 */
    char *body = malloc(content_len + 1);
    if (!body) {
        ESP_LOGE(TAG, "malloc(%d) 失败", content_len + 1);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return NULL;
    }

    int read = esp_http_client_read_response(client, body, content_len);
    if (read < 0) {
        ESP_LOGW(TAG, "HTTP read 失败: %d", read);
        free(body);
        body = NULL;
    } else {
        body[read] = '\0';
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return body;
}


/**
 * @brief 解析注入命令并写入 g_sensor_data
 *
 * JSON 格式: {"dht11_t":39,"dht11_h":60,"ds18b20_t":38.5,"mq135_v":2.8,
 *              "mq135_do":0,"photo_raw":2000,"photo_do":1}
 *
 * 使用 sscanf 手动提取字段 (与 udp_sim_command_task 相同逻辑),
 * 避免引入 cJSON 增加 flash 负担。
 */
static void apply_sim_command(const char *json)
{
    int sim_dht11_t = 25, sim_dht11_h = 60;
    float sim_ds18b20 = 25.0f;
    int sim_mq135_do = 1, sim_photo_do = 1, sim_photo_raw = 2000;
    float sim_mq135_v = 1.2f;
    int parsed = 0;

    char *p;
    if ((p = strstr(json, "\"dht11_t\":")))  sscanf(p + 10, "%d", &sim_dht11_t),  parsed++;
    if ((p = strstr(json, "\"dht11_h\":")))  sscanf(p + 10, "%d", &sim_dht11_h),  parsed++;
    if ((p = strstr(json, "\"ds18b20_t\":"))) sscanf(p + 12, "%f", &sim_ds18b20), parsed++;
    if ((p = strstr(json, "\"mq135_v\":")))  sscanf(p + 10, "%f", &sim_mq135_v),  parsed++;
    if ((p = strstr(json, "\"mq135_do\":"))) sscanf(p + 10, "%d", &sim_mq135_do), parsed++;
    if ((p = strstr(json, "\"photo_raw\":"))) sscanf(p + 12, "%d", &sim_photo_raw), parsed++;
    if ((p = strstr(json, "\"photo_do\":"))) sscanf(p + 10, "%d", &sim_photo_do), parsed++;

    if (parsed == 0) {
        ESP_LOGW(TAG, "无法解析仿真命令: %s", json);
        return;
    }

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

    ESP_LOGI(TAG, "📥 仿真注入: Tdht=%d H=%d%% Tds=%.1f MQv=%.2f MQdo=%d PR=%d Pdo=%d",
             sim_dht11_t, sim_dht11_h, sim_ds18b20,
             sim_mq135_v, sim_mq135_do, sim_photo_raw, sim_photo_do);
}


/**
 * @brief 恢复真实传感器模式
 */
static void apply_reset_command(void)
{
    if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        g_sensor_data.sim_active = 0;
        xSemaphoreGive(g_sensor_mutex);
    }
    ESP_LOGI(TAG, "🔄 仿真模式已关闭, 恢复真实传感器");
}


/* ==================== 主任务 ==================== */

void sim_poll_task(void *arg)
{
    (void)arg;

    /* ===== 1. 等待 Wi-Fi 连接 ===== */
    ESP_LOGI(TAG, "等待 Wi-Fi 连接...");
    {
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
            ESP_LOGE(TAG, "Wi-Fi 连接超时 (20s), 任务退出");
            vTaskDelete(NULL);
            return;
        }
    }
    ESP_LOGI(TAG, "轮询就绪 → %s (间隔 %d ms)", SIM_POLL_URL, SIM_POLL_MS);

    /* ===== 2. 主轮询循环 ===== */
    int last_seq = 0;
    while (1) {
        /* 构建带 seq 的 URL */
        char url[256];
        snprintf(url, sizeof(url), "%s?seq=%d", SIM_POLL_URL, last_seq);

        char *body = http_get_body(url);
        if (!body) {
            /* HTTP 失败, 等下一轮 */
            vTaskDelay(pdMS_TO_TICKS(SIM_POLL_MS));
            continue;
        }

        /* 检查 seq 字段: {"seq":N,"data":{...}} */
        int seq = 0;
        char *seq_ptr = strstr(body, "\"seq\":");
        if (seq_ptr) {
            sscanf(seq_ptr + 5, "%d", &seq);
        }

        /* 检查 data 是否为 null (无新命令) */
        char *data_null = strstr(body, "\"data\":null");
        if (data_null) {
            /* 无新命令 — 静默跳过 */
            if (seq > last_seq) last_seq = seq;
            free(body);
            vTaskDelay(pdMS_TO_TICKS(SIM_POLL_MS));
            continue;
        }

        /* 确认 seq 更新 */
        if (seq <= last_seq) {
            free(body);
            vTaskDelay(pdMS_TO_TICKS(SIM_POLL_MS));
            continue;
        }
        last_seq = seq;

        /* 检查是否是 reset 命令 */
        if (strstr(body, "\"cmd\":\"reset\"") || strstr(body, "\"cmd\": \"reset\"")) {
            apply_reset_command();
            free(body);
            vTaskDelay(pdMS_TO_TICKS(SIM_POLL_MS));
            continue;
        }

        /* 应用注入命令 */
        apply_sim_command(body);
        free(body);

        vTaskDelay(pdMS_TO_TICKS(SIM_POLL_MS));
    }
}
