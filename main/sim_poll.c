/**
 * @file sim_poll.c
 * @brief 仿真命令 + 报警配置 HTTP 轮询实现 (反转通信方向)
 *
 * ESP32 主动 HTTP GET 拉取 VPS 上暂存的命令，替代旧方案中
 * VPS → ESP32 的 UDP 入站（被 NAT 阻挡）。
 *
 * 轮询端点:
 *   - /api/sim/poll          仿真注入 & reset
 *   - /api/alarm/config/poll 报警阈值配置
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
#define SIM_POLL_URL       "http://38.55.199.220:8001/api/sim/poll"
#define ALARM_CFG_POLL_URL "http://38.55.199.220:8001/api/alarm/config/poll"
#define SIM_POLL_MS        3000    /* 仿真注入 轮询间隔 3s */
#define ALARM_CFG_POLL_MS  10000   /* 报警配置 轮询间隔 10s (低频) */

/* HTTP 响应缓冲区 — 命令很小 (<1KB) */
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
        esp_http_client_close(client);
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
 * JSON 格式: {"dht11_t":39,"dht11_h":60,"ds18b20_t":38.5,"mq135_raw":3500,
 *              "mq135_do":0,"photo_raw":2000,"photo_do":1}
 * 兼容旧字段 mq135_v, 优先使用 mq135_raw (ADC 0~4095)
 *
 * 使用 sscanf 手动提取字段 (与 udp_sim_command_task 相同逻辑),
 * 避免引入 cJSON 增加 flash 负担。
 */
static void apply_sim_command(const char *json)
{
    int sim_dht11_t = 25, sim_dht11_h = 60;
    float sim_ds18b20 = 25.0f;
    int sim_mq135_do = 1, sim_photo_do = 1, sim_photo_raw = 2000;
    int   sim_mq135_raw = 1500;
    float sim_mq135_v = 1.2f;
    int parsed = 0;

    char *p;
    if ((p = strstr(json, "\"dht11_t\":")))  sscanf(p + 10, "%d", &sim_dht11_t),  parsed++;
    if ((p = strstr(json, "\"dht11_h\":")))  sscanf(p + 10, "%d", &sim_dht11_h),  parsed++;
    if ((p = strstr(json, "\"ds18b20_t\":"))) sscanf(p + 12, "%f", &sim_ds18b20), parsed++;
    /* mq135_raw (新) 优先, mq135_v (旧) 做 fallback */
    if ((p = strstr(json, "\"mq135_raw\":")))
        { sscanf(p + 12, "%d", &sim_mq135_raw); sim_mq135_v = sim_mq135_raw * 3.3f / 4095.0f; parsed++; }
    else if ((p = strstr(json, "\"mq135_v\":")))
        { sscanf(p + 10, "%f", &sim_mq135_v); parsed++; }
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

    ESP_LOGI(TAG, "📥 仿真注入: Tdht=%d H=%d%% Tds=%.1f MQraw=%d MQdo=%d PR=%d Pdo=%d",
             sim_dht11_t, sim_dht11_h, sim_ds18b20,
             sim_mq135_raw, sim_mq135_do, sim_photo_raw, sim_photo_do);
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


/**
 * @brief 解析报警配置命令并更新 g_sensor_data + 持久化到 NVS
 *
 * JSON 格式: {"cmd":"config","mq135_alarm_src":0,"photo_alarm_src":0,
 *              "mq135_ao_dir":0,"photo_ao_dir":1,"mq135_ao_threshold":3100,
 *              "photo_ao_threshold":1000,"dht11_temp_high":35,
 *              "dht11_humi_high":85,"ds18b20_temp_high":35.0,
 *              "temp_humi_alarm_enabled":1}
 *
 * 字段均为可选，提取到才更新。解析逻辑与 udp_sim_command_task 中的
 * config 处理保持镜像一致。
 */
static void apply_alarm_config(const char *json)
{
    char *p;
    int   tmp_i = 0;
    float tmp_f = 0.0f;
    int   tmp_raw = 0;
    int   updated = 0;

    if (xSemaphoreTake(g_sensor_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        if ((p = strstr(json, "\"mq135_alarm_src\":")))
            { if (sscanf(p + 18, "%d", &tmp_i) == 1) { g_sensor_data.mq135_alarm_src = (alarm_source_t)tmp_i; updated++; } }
        if ((p = strstr(json, "\"photo_alarm_src\":")))
            { if (sscanf(p + 18, "%d", &tmp_i) == 1) { g_sensor_data.photo_alarm_src = (alarm_source_t)tmp_i; updated++; } }
        if ((p = strstr(json, "\"mq135_ao_dir\":")))
            { if (sscanf(p + 15, "%d", &tmp_i) == 1) { g_sensor_data.mq135_ao_dir = (ao_trigger_dir_t)tmp_i; updated++; } }
        if ((p = strstr(json, "\"photo_ao_dir\":")))
            { if (sscanf(p + 15, "%d", &tmp_i) == 1) { g_sensor_data.photo_ao_dir = (ao_trigger_dir_t)tmp_i; updated++; } }
        if ((p = strstr(json, "\"mq135_ao_threshold\":")))
            { if (sscanf(p + 21, "%d", &tmp_raw) == 1) { g_sensor_data.mq135_ao_threshold = tmp_raw; updated++; } }
        if ((p = strstr(json, "\"photo_ao_threshold\":")))
            { if (sscanf(p + 21, "%d", &tmp_i) == 1) { g_sensor_data.photo_ao_threshold = tmp_i; updated++; } }
        if ((p = strstr(json, "\"dht11_temp_high\":")))
            { if (sscanf(p + 18, "%d", &tmp_i) == 1) { g_sensor_data.dht11_temp_high = tmp_i; updated++; } }
        if ((p = strstr(json, "\"dht11_humi_high\":")))
            { if (sscanf(p + 18, "%d", &tmp_i) == 1) { g_sensor_data.dht11_humi_high = tmp_i; updated++; } }
        if ((p = strstr(json, "\"ds18b20_temp_high\":")))
            { if (sscanf(p + 20, "%f", &tmp_f) == 1) { g_sensor_data.ds18b20_temp_high = tmp_f; updated++; } }
        if ((p = strstr(json, "\"temp_humi_alarm_enabled\":")))
            { if (sscanf(p + 26, "%d", &tmp_i) == 1) { g_sensor_data.temp_humi_alarm_enabled = tmp_i; updated++; } }
        xSemaphoreGive(g_sensor_mutex);
    }

    if (updated == 0) {
        ESP_LOGW(TAG, "报警配置解析失败: %s", json);
        return;
    }

    /* 异步持久化到 NVS */
    save_alarm_config_to_nvs();

    ESP_LOGI(TAG, "⚙️ 报警配置已更新: mq_src=%d mq_thr=%draw ph_src=%d ph_thr=%d dht_t=%d dht_h=%d ds_t=%.1f temp_en=%d",
             g_sensor_data.mq135_alarm_src, g_sensor_data.mq135_ao_threshold,
             g_sensor_data.photo_alarm_src, g_sensor_data.photo_ao_threshold,
             g_sensor_data.dht11_temp_high, g_sensor_data.dht11_humi_high,
             g_sensor_data.ds18b20_temp_high, g_sensor_data.temp_humi_alarm_enabled);
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
        while (1) {
            if (sta_netif && esp_netif_is_netif_up(sta_netif)) {
                esp_netif_get_ip_info(sta_netif, &ip_info);
                if (ip_info.ip.addr != 0) break;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            wait++;
            /* 每 20s 打一次日志，避免静默 */
            if (wait % 200 == 0) {
                ESP_LOGW(TAG, "Wi-Fi 仍未就绪，继续等待...");
            }
        }
    }
    ESP_LOGI(TAG, "轮询就绪 → sim=%s (%dms) | cfg=%s (%dms)",
             SIM_POLL_URL, SIM_POLL_MS, ALARM_CFG_POLL_URL, ALARM_CFG_POLL_MS);

    /* ===== 2. 主轮询循环 ===== */
    int last_sim_seq = 0;
    int last_cfg_seq = 0;
    int tick = 0;  /* 循环计数，用于低频轮询 */

    int fail_count = 0;  /* 连续失败计数器，用于退避 */
    while (1) {
        /* ── 2a. 仿真注入轮询 (每次循环) ── */
        {
            char url[256];
            snprintf(url, sizeof(url), "%s?seq=%d", SIM_POLL_URL, last_sim_seq);

            char *body = http_get_body(url);
            if (body) {
                /* 检查 seq 字段: {"seq":N,"data":{...}} */
                int seq = 0;
                char *seq_ptr = strstr(body, "\"seq\":");
                if (seq_ptr) {
                    sscanf(seq_ptr + 6, "%d", &seq);
                }

                /* 检查 data 是否为 null (无新命令) */
                char *data_null = strstr(body, "\"data\":null");
                if (!data_null && seq > last_sim_seq) {
                    last_sim_seq = seq;

                    /* 检查是否是 reset 命令 */
                    if (strstr(body, "\"cmd\":\"reset\"") || strstr(body, "\"cmd\": \"reset\"")) {
                        apply_reset_command();
                    } else {
                        apply_sim_command(body);
                    }
                } else {
                    /* 无新命令 — 仅更新 seq 引用 */
                    if (seq > last_sim_seq) last_sim_seq = seq;
                }
                free(body);
                fail_count = 0;  /* 成功则重置退避 */
            } else {
                fail_count++;
            }
        }

        /* ── 2b. 报警配置轮询 (每 ALARM_CFG_POLL_MS / SIM_POLL_MS 次循环一次) ── */
        int cfg_interval = ALARM_CFG_POLL_MS / SIM_POLL_MS;
        if (cfg_interval < 1) cfg_interval = 1;
        if (tick % cfg_interval == 0) {
            static int cfg_first_poll = 1;  /* 首次轮询诊断标记 */

            char url[256];
            snprintf(url, sizeof(url), "%s?seq=%d", ALARM_CFG_POLL_URL, last_cfg_seq);

            char *body = http_get_body(url);
            if (body) {
                int seq = 0;
                char *seq_ptr = strstr(body, "\"seq\":");
                if (seq_ptr) {
                    sscanf(seq_ptr + 6, "%d", &seq);
                }

                char *data_null = strstr(body, "\"data\":null");
                /* ★ v3.7 启动同步: last_cfg_seq==0 时允许 seq==0（首次从 SQLite 同步） */
                int is_new = (last_cfg_seq == 0) ? (seq >= last_cfg_seq)
                                                 : (seq > last_cfg_seq);
                if (!data_null && is_new) {
                    last_cfg_seq = seq;
                    cfg_first_poll = 0;
                    /* 必须包含 cmd:config 才处理 */
                    if (strstr(body, "\"cmd\":\"config\"") || strstr(body, "\"cmd\": \"config\"")) {
                        apply_alarm_config(body);
                    }
                } else {
                    if (seq > last_cfg_seq) last_cfg_seq = seq;
                    /* 首次轮询诊断: 打一次日志确认链路通畅 */
                    if (cfg_first_poll) {
                        if (data_null) {
                            ESP_LOGI(TAG, "报警配置轮询: 无新配置 (seq=%d), 链路正常, 后续静默", seq);
                        } else {
                            ESP_LOGI(TAG, "报警配置轮询就绪 (seq=%d, 等待配置变更)", seq);
                        }
                        cfg_first_poll = 0;
                    }
                }
                free(body);
            } else {
                /* HTTP 请求失败时仅首次打一次警告 */
                if (cfg_first_poll) {
                    ESP_LOGW(TAG, "报警配置轮询: 首次 HTTP 请求失败, %dms 后重试", ALARM_CFG_POLL_MS);
                }
            }
        }

        tick++;
        /* 退避：连续失败时逐渐延长间隔，避免 VPS rate-limit 封 IP */
        int delay = SIM_POLL_MS;
        if (fail_count > 5)  delay = 5000;
        else if (fail_count > 2) delay = 2000;
        /* 超过 20 次连续失败 → 冷却 60s，等 VPS 解除 rate limit */
        if (fail_count > 20) {
            ESP_LOGW(TAG, "连续 %d 次 HTTP 失败, 暂停 60s 等待 VPS 恢复...", fail_count);
            vTaskDelay(pdMS_TO_TICKS(60000));
            fail_count = 0;  /* 冷却后重置 */
        }
        vTaskDelay(pdMS_TO_TICKS(delay));
    }
}
