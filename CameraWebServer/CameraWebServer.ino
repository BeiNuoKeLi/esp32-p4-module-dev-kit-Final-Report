#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// ===========================
// Select camera model in board_config.h
// ===========================
#include "board_config.h"

// ===========================
// Enter your WiFi credentials
// ===========================
const char *ssid = "BeiNuoKeLi_Mi";
const char *password = "BEINUOKELI";

void startCameraServer();
void setupLedFlash();

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(false);  // 关闭WiFi调试输出，节省CPU
  Serial.println();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;  // for streaming
  //config.pixel_format = PIXFORMAT_RGB565; // for face detection/recognition
  config.fb_location = CAMERA_FB_IN_PSRAM;

  // ★ HVGA 480x320: 2xQVGA像素，互联网推流文件适中 ~8-10KB，FPS稳定
  // ★ jpeg_quality=15: 高画质, ~8KB/帧 ≈8分片@1024, 预计15-20fps
  if (psramFound()) {
    config.frame_size = FRAMESIZE_HVGA;     // ★ 480x320 折中 (QVGA太糊/VGA闪屏)
    config.jpeg_quality = 15;              // ★ 高画质, ~8KB/帧 ≈8分片
    config.fb_count = 2;                   // ★ 双缓冲: 连续DMA，帧立即可取
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY; // ★ 仅取完整帧
  } else {
    // 无 PSRAM 时降至最低分辨率 + 单缓冲
    config.frame_size = FRAMESIZE_QQVGA;
    config.jpeg_quality = 40;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  // camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    // ★ 闪烁板载 LED 指示故障（如果可用）
  #if defined(LED_GPIO_NUM)
    pinMode(LED_GPIO_NUM, OUTPUT);
    for (;;) {
      digitalWrite(LED_GPIO_NUM, HIGH);
      delay(250);
      digitalWrite(LED_GPIO_NUM, LOW);
      delay(250);
    }
  #else
    for (;;) { delay(1000); }
  #endif
  }

  sensor_t *s = esp_camera_sensor_get();
  // OV3660 传感器特殊处理
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);        // flip it back
    s->set_brightness(s, 1);   // up the brightness just a bit
    s->set_saturation(s, -2);  // lower the saturation
  }
  // ★ OV2640 传感器 → 针对二维码识别优化
  if (s->id.PID == OV2640_PID) {
    s->set_brightness(s, 0);         // 中性亮度, 避免过曝
    s->set_contrast(s, 2);           // ★ 拉高对比度 → 黑白边缘更锐利, pyzbar 易识别
    s->set_saturation(s, -1);        // QR 是黑白的, 降低饱和度减少色彩噪声
    s->set_gain_ctrl(s, 1);          // 开启自动增益
    s->set_agc_gain(s, 0);           // 手动增益归零 (AGC 自动决策)
    s->set_aec2(s, 1);               // ★ 快速自动曝光 → 对准 QR 后 2-3 帧稳定
    s->set_ae_level(s, 0);           // 曝光居中
    s->set_gainceiling(s, (gainceiling_t)0); // GAINCEILING_2X → 限制增益上限, 防暗部噪声
    s->set_wb_mode(s, 0);            // 自动白平衡
    s->set_dcw(s, 1);               // 开窗缩小画幅
    s->set_raw_gma(s, 1);           // 使用传感器 Gamma
  }

#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
  s->set_vflip(s, 1);
  s->set_hmirror(s, 1);
#endif

#if defined(CAMERA_MODEL_ESP32S3_EYE)
  s->set_vflip(s, 1);
#endif

#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  WiFi.begin(ssid, password);
  WiFi.setSleep(false);

  Serial.print("WiFi connecting");
  unsigned long wifi_start = millis();
  const unsigned long WIFI_TIMEOUT = 30000;  // ★ 30 秒超时，防止永久卡死
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - wifi_start > WIFI_TIMEOUT) {
      Serial.println("\nWiFi 连接超时, 重启设备...");
      delay(1000);
      ESP.restart();
    }
  }
  Serial.println("");
  Serial.println("WiFi connected");

  startCameraServer();

  Serial.print("Camera Ready! Use 'http://");
  Serial.print(WiFi.localIP());
  Serial.println("' to connect");
}

// ★ v4.0 UDP 分片推流: WiFiUDP 直连 VPS:8003
//    协议头 8B [Magic(0xAA55) + FrameID(u16) + ChunkIdx(u16) + TotalChunks(u16)]
//    单帧 3-8KB → 2-6 个 UDP 包（每包 ≤1480B），零连接开销、无 TCP 窗口限制
//    UDP 吞吐量实测 238Mbps vs TCP 2Mbps (119x)，不受跨海 RTT 影响
#define CAM_STREAM_HOST    "38.55.199.220"
#define CAM_STREAM_PORT    8003
#define UDP_CHUNK_SIZE     1024  // ★ 安全互联网 MTU (1400B大包跨网被丢弃)

#include <WiFiUdp.h>

static WiFiUDP udpClient;
static uint16_t frame_id = 0;          // 帧序号 0-65535 循环
static unsigned long frame_cnt = 0;    // 3s 窗口帧计数
static unsigned long drop_cnt = 0;     // 3s 窗口丢帧计数
static unsigned long last_diag_ms = 0;

void loop() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb || fb->len == 0) {
    if (fb) esp_camera_fb_return(fb);
    return;
  }

  uint32_t jpeg_len = fb->len;         // ★ 保存长度 (fb_return 后 fb 不可用)
  uint16_t total_chunks = (jpeg_len + UDP_CHUNK_SIZE - 1) / UDP_CHUNK_SIZE;
  bool all_ok = true;

  // UDP 分片推流 (无连接态，无需重连/心跳)
  for (uint16_t i = 0; i < total_chunks; i++) {
    uint16_t offset = i * UDP_CHUNK_SIZE;
    uint16_t chunk_len = (i == total_chunks - 1) ? (jpeg_len - offset) : UDP_CHUNK_SIZE;

    // 打包 8B 协议头 (大端序，与 camera_protocol.py 一致)
    uint8_t header[8];
    header[0] = 0xAA; header[1] = 0x55;                                   // Magic
    header[2] = (frame_id >> 8) & 0xFF;  header[3] = frame_id & 0xFF;    // FrameID
    header[4] = (i >> 8) & 0xFF;         header[5] = i & 0xFF;           // ChunkIdx
    header[6] = (total_chunks >> 8) & 0xFF; header[7] = total_chunks & 0xFF; // TotalChunks

    if (!udpClient.beginPacket(CAM_STREAM_HOST, CAM_STREAM_PORT)) {
      all_ok = false; break;
    }
    udpClient.write(header, 8);
    udpClient.write(fb->buf + offset, chunk_len);
    if (!udpClient.endPacket()) {
      all_ok = false; break;
    }
    delay(2);  // ★ WiFi栈退避: 防16分片连续发送冲爆缓冲区
  }

  frame_id++;
  if (all_ok) {
    frame_cnt++;
  } else {
    drop_cnt++;
  }

  esp_camera_fb_return(fb);

  // 每 3s 输出诊断
  unsigned long now = millis();
  if (now - last_diag_ms > 3000) {
    last_diag_ms = now;
    float fps = frame_cnt / 3.0;
    Serial.printf("[DIAG] 帧=%lu | FPS≈%.1f | 丢=%lu | 分片=%u | JPEG=%uB\n",
                  frame_cnt, fps, drop_cnt, total_chunks, jpeg_len);
    frame_cnt = 0;
    drop_cnt = 0;
  }

  // ★ FPS 限制 15 帧: 帧间留空隙确保WiFi分片全部发完，防止帧间混叠丢包
  static unsigned long last_frame_ms = 0;
  const unsigned long TARGET_INTERVAL = 1000 / 15;  // 66ms
  unsigned long elapsed = millis() - last_frame_ms;
  if (elapsed < TARGET_INTERVAL) {
    delay(TARGET_INTERVAL - elapsed);
  }
  last_frame_ms = millis();
}
