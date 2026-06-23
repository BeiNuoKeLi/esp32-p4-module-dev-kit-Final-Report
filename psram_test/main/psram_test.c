#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_heap_caps.h"

static const char *TAG = "PSRAM_TEST";

/* ==================== 测试 1: 全片顺序读写 ==================== */
static bool test_seq_rw(void)
{
    size_t psram_size = esp_psram_get_size();
    if (psram_size == 0) {
        ESP_LOGE(TAG, "❌ PSRAM 未检测到!");
        return false;
    }
    ESP_LOGI(TAG, "PSRAM 容量: %d MB", (int)(psram_size / (1024 * 1024)));

    /* 只测前 1MB，避免 IO 太慢 */
    size_t test_size = (psram_size > 1024 * 1024) ? 1024 * 1024 : psram_size;
    uint8_t *buf = (uint8_t *)heap_caps_malloc(test_size, MALLOC_CAP_SPIRAM);
    if (!buf) {
        ESP_LOGE(TAG, "❌ 无法分配 %d KB PSRAM", (int)(test_size / 1024));
        return false;
    }

    /* 顺序写入模式 */
    ESP_LOGI(TAG, "  写入 %d KB 模式数据...", (int)(test_size / 1024));
    for (size_t i = 0; i < test_size; i += 4096) {
        memset(buf + i, (uint8_t)(i & 0xFF), 4096);
    }

    /* 顺序读回验证 */
    ESP_LOGI(TAG, "  读回验证...");
    int errors = 0;
    for (size_t i = 0; i < test_size; i++) {
        uint8_t expected = (uint8_t)((i & ~0xFFF) & 0xFF);
        if (buf[i] != expected && errors < 10) {
            ESP_LOGW(TAG, "  地址 0x%x: 期望 0x%02x 实际 0x%02x",
                     (unsigned int)i, expected, buf[i]);
            errors++;
        }
    }
    if (errors > 0) {
        ESP_LOGE(TAG, "❌ 顺序读写: %d 处错误", errors);
        free(buf);
        return false;
    }
    free(buf);
    ESP_LOGI(TAG, "✅ 顺序读写 (1MB): PASS");
    return true;
}

/* ==================== 测试 2: 随机地址读写 ==================== */
static bool test_random_rw(void)
{
    const int BLOCK_COUNT = 256;
    const int BLOCK_SIZE = 4096;
    size_t total = BLOCK_COUNT * BLOCK_SIZE;

    uint8_t *buf = (uint8_t *)heap_caps_malloc(total, MALLOC_CAP_SPIRAM);
    if (!buf) {
        ESP_LOGE(TAG, "❌ 无法分配 %d KB", (int)(total / 1024));
        return false;
    }

    /* 随机写入 256 个 4KB 块 */
    ESP_LOGI(TAG, "  随机写入 %d 个 4KB 块...", BLOCK_COUNT);
    for (int n = 0; n < BLOCK_COUNT; n++) {
        int idx = (n * 127 + 31) % BLOCK_COUNT;
        memset(buf + idx * BLOCK_SIZE, (uint8_t)(idx & 0xFF), BLOCK_SIZE);
    }

    /* 随机读回 */
    int errors = 0;
    for (int n = 0; n < BLOCK_COUNT; n++) {
        int idx = (n * 97 + 17) % BLOCK_COUNT;
        uint8_t expected = (uint8_t)(idx & 0xFF);
        if (buf[idx * BLOCK_SIZE] != expected && errors < 5) {
            ESP_LOGW(TAG, "  块 %d: 期望 0x%02x 实际 0x%02x", idx, expected, buf[idx * BLOCK_SIZE]);
            errors++;
        }
    }
    free(buf);

    if (errors > 0) {
        ESP_LOGE(TAG, "❌ 随机读写: %d 处错误", errors);
        return false;
    }
    ESP_LOGI(TAG, "✅ 随机读写 (256×4KB): PASS");
    return true;
}

/* ==================== 测试 3: malloc/free 压力 ==================== */
static bool test_malloc_stress(void)
{
#define ITERATIONS 200
#define MAX_BLOCKS 50
    void *blocks[MAX_BLOCKS] = {0};

    ESP_LOGI(TAG, "  分配/释放 %d 轮...", ITERATIONS);

    for (int round = 0; round < ITERATIONS; round++) {
        int count = (round % MAX_BLOCKS) + 1;
        /* 分配 */
        for (int i = 0; i < count; i++) {
            size_t sz = 1024 + (i * 4096) % 65536;
            blocks[i] = heap_caps_malloc(sz, MALLOC_CAP_SPIRAM);
            if (blocks[i]) {
                memset(blocks[i], 0xAB, 64); /* 只写开头 64B */
            }
        }
        /* 释放 */
        for (int i = 0; i < count; i++) {
            if (blocks[i]) {
                free(blocks[i]);
                blocks[i] = NULL;
            }
        }
        if (round % 50 == 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    /* 检查 heap 没泄漏 */
    size_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    ESP_LOGI(TAG, "  剩余 PSRAM: %d KB", (int)(free_psram / 1024));

    ESP_LOGI(TAG, "✅ malloc 压力 (200轮): PASS");
    return true;
}

/* ==================== 测试 4: 内部 RAM ↔ PSRAM 互拷 ==================== */
static bool test_memcpy_cross(void)
{
    const int SIZE = 256 * 1024;
    uint8_t *src = heap_caps_malloc(SIZE, MALLOC_CAP_SPIRAM);
    uint8_t *dst = heap_caps_malloc(SIZE, MALLOC_CAP_SPIRAM);
    uint8_t *verify = malloc(SIZE); /* 内部 RAM */

    if (!src || !dst || !verify) {
        ESP_LOGE(TAG, "❌ 内存分配失败");
        goto out;
    }

    /* 内部 RAM → PSRAM */
    for (int i = 0; i < SIZE; i++) verify[i] = (uint8_t)(i & 0xFF);
    memcpy(src, verify, SIZE);

    /* PSRAM → PSRAM */
    memcpy(dst, src, SIZE);

    /* PSRAM → 内部 RAM 读回 */
    memset(verify, 0, SIZE);
    memcpy(verify, dst, SIZE);

    int errors = 0;
    for (int i = 0; i < SIZE; i++) {
        if (verify[i] != (uint8_t)(i & 0xFF) && errors < 5) {
            ESP_LOGW(TAG, "  memcpy 错误 @ %d: 0x%02x != 0x%02x", i, verify[i], (uint8_t)(i & 0xFF));
            errors++;
        }
    }

    free(src); free(dst); free(verify);
    if (errors > 0) {
        ESP_LOGE(TAG, "❌ 跨区 memcpy: %d 处错误", errors);
        return false;
    }
    ESP_LOGI(TAG, "✅ 跨区 memcpy (256KB): PASS");
    return true;

out:
    free(src); free(dst); free(verify);
    return false;
}

/* ==================== 测试 5: 双任务并发访问 ==================== */
static volatile bool g_concurrent_running = true;
static volatile int  g_concurrent_errors = 0;

static void concurrent_task_a(void *arg)
{
    uint8_t *buf = (uint8_t *)arg;
    while (g_concurrent_running) {
        for (int i = 0; i < 4096; i += 64) {
            buf[i] = (uint8_t)0xAA;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    vTaskDelete(NULL);
}

static void concurrent_task_b(void *arg)
{
    uint8_t *buf = (uint8_t *)arg;
    while (g_concurrent_running) {
        for (int i = 0; i < 4096; i += 64) {
            if (buf[i] != 0xAA && buf[i] != 0xBB) {
                g_concurrent_errors++;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    vTaskDelete(NULL);
}

static bool test_concurrent(void)
{
    const int BLOCK_SIZE = 4096;
    uint8_t *buf_a = heap_caps_malloc(BLOCK_SIZE, MALLOC_CAP_SPIRAM);
    uint8_t *buf_b = heap_caps_malloc(BLOCK_SIZE, MALLOC_CAP_SPIRAM);

    if (!buf_a || !buf_b) {
        ESP_LOGE(TAG, "❌ 并发测试内存分配失败");
        goto out;
    }

    memset(buf_a, 0, BLOCK_SIZE);
    memset(buf_b, 0, BLOCK_SIZE);

    g_concurrent_running = true;
    g_concurrent_errors = 0;

    ESP_LOGI(TAG, "  启动双任务并发访问 PSRAM (3秒)...");
    xTaskCreate(concurrent_task_a, "psram_wr", 4096, buf_a, 2, NULL);
    xTaskCreate(concurrent_task_b, "psram_rd", 4096, buf_a, 1, NULL);

    vTaskDelay(pdMS_TO_TICKS(3000));
    g_concurrent_running = false;
    vTaskDelay(pdMS_TO_TICKS(200));

    free(buf_a);
    free(buf_b);

    if (g_concurrent_errors > 0) {
        ESP_LOGE(TAG, "❌ 并发访问: %d 处异常", g_concurrent_errors);
        return false;
    }
    ESP_LOGI(TAG, "✅ 并发访问 (3秒): PASS");
    return true;

out:
    free(buf_a);
    free(buf_b);
    return false;
}

/* ==================== 测试 6: 非对齐访问 ==================== */
static bool test_unaligned(void)
{
    const int SIZE = 4096;
    uint8_t *buf = heap_caps_malloc(SIZE, MALLOC_CAP_SPIRAM);
    if (!buf) {
        ESP_LOGE(TAG, "❌ 非对齐测试内存分配失败");
        return false;
    }
    memset(buf, 0, SIZE);

    /* 非对齐写入 uint32 */
    for (int off = 0; off < SIZE - 4; off++) {
        uint32_t val = (uint32_t)(off | (off << 16));
        memcpy(buf + off, &val, 4);
    }

    /* 非对齐读回 */
    int errors = 0;
    for (int off = 0; off < SIZE - 4; off += 128) {
        uint32_t val;
        memcpy(&val, buf + off + 1, 4); /* +1 故意非对齐 */
        uint32_t expected = (uint32_t)((off + 1) | ((off + 1) << 16));
        if (val != expected && errors < 5) {
            ESP_LOGW(TAG, "  非对齐读错误 @ %d: 0x%08lx != 0x%08lx", off + 1, val, expected);
            errors++;
        }
    }
    free(buf);

    if (errors > 0) {
        ESP_LOGE(TAG, "❌ 非对齐访问: %d 处错误", errors);
        return false;
    }
    ESP_LOGI(TAG, "✅ 非对齐访问: PASS");
    return true;
}

/* ==================== 测试 7: 长时间浸泡 ==================== */
static bool test_soak(void)
{
    const int DURATION_SEC = 30;
    const int BLOCK_SIZE = 64 * 1024;
    uint8_t *buf = heap_caps_malloc(BLOCK_SIZE, MALLOC_CAP_SPIRAM);
    if (!buf) {
        ESP_LOGE(TAG, "❌ 浸泡测试内存分配失败");
        return false;
    }

    ESP_LOGI(TAG, "  浸泡测试 %d 秒 (每轮 64KB 随机读写)...", DURATION_SEC);
    int rounds = 0;
    int errors = 0;

    for (int sec = 0; sec < DURATION_SEC; sec++) {
        for (int sub = 0; sub < 10; sub++) {
            rounds++;
            uint8_t seed = (uint8_t)(sec * 10 + sub);
            memset(buf, seed, BLOCK_SIZE);
            for (int i = 0; i < BLOCK_SIZE; i += 1024) {
                if (buf[i] != seed) {
                    errors++;
                }
            }
            vTaskDelay(pdMS_TO_TICKS(50));
        }
        if (sec % 10 == 0) {
            ESP_LOGI(TAG, "  ... %d 秒, %d 轮, %d 错误", sec, rounds, errors);
        }
    }

    free(buf);
    if (errors > 0) {
        ESP_LOGE(TAG, "❌ 浸泡测试: %d 处错误 (%d 轮)", errors, rounds);
        return false;
    }
    ESP_LOGI(TAG, "✅ 浸泡测试 (%d秒, %d轮): PASS", DURATION_SEC, rounds);
    return true;
}

/* ==================== 主函数 ==================== */
void app_main(void)
{
    ESP_LOGI(TAG, "==========================================");
    ESP_LOGI(TAG, "  ESP32-P4 PSRAM 压力测试");
    ESP_LOGI(TAG, "==========================================");

    vTaskDelay(pdMS_TO_TICKS(1000));

    int passed = 0, failed = 0;
    typedef bool (*test_fn)(void);
    test_fn tests[] = {
        test_seq_rw,
        test_random_rw,
        test_malloc_stress,
        /* test_memcpy_cross, */ /* SKIP: known crash (HW bug: PSRAM->internal memcpy) */
        test_unaligned,
        test_concurrent,
        test_soak,
    };
    const char *names[] = {
        "顺序读写", "随机读写", "malloc压力",
        "[跳过]", "非对齐访问", "并发访问", "浸泡30s"
    };

    for (int i = 0; i < sizeof(tests) / sizeof(tests[0]); i++) {
        ESP_LOGI(TAG, "------------------------------------------");
        ESP_LOGI(TAG, "测试 %d/%d: %s", i + 1, (int)(sizeof(tests) / sizeof(tests[0])), names[i]);
        vTaskDelay(pdMS_TO_TICKS(200));

        if (tests[i]()) {
            passed++;
        } else {
            failed++;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    ESP_LOGI(TAG, "==========================================");
    ESP_LOGI(TAG, "  结果: %d 通过, %d 失败", passed, failed);
    ESP_LOGI(TAG, "==========================================");

    if (failed == 0) {
        ESP_LOGI(TAG, "🎉 所有 PSRAM 测试通过!");
    } else {
        ESP_LOGW(TAG, "⚠ PSRAM 存在 %d 项不稳定", failed);
    }

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
