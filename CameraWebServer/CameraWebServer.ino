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

  // ★ HVGA 480x320: 二维码识别需要足够像素密度
  // ★ fb_count=2: 连续 I2S DMA 模式，esp_camera_fb_get() 直接从队列取帧
  // ★ jpeg_quality=20: 互联网 TCP 推流瓶颈是文件大小而非压缩时间
  //    q12→13KB→2fps | q20→7KB→5fps | q35→4KB 但块状伪影→二维码识别失败
  if (psramFound()) {
    config.frame_size = FRAMESIZE_HVGA;    // 480x320, 二维码识别最低需求
    config.jpeg_quality = 20;              // 平衡画质与互联网推流吞吐
    config.fb_count = 2;                   // ★ 双缓冲: 连续DMA，帧立即可取
    config.grab_mode = CAMERA_GRAB_LATEST; // 始终取最新帧
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

// ★ v3.8 TCP 二进制推流: 仿 MJPEG stream handler 的帧驱动模式
//    WiFiClient 直连 VPS:8003，帧格式 [2B big-endian len][JPEG]
//    零 HTTP 开销、零心率阻塞、零延迟抖动
#define CAM_STREAM_HOST    "38.55.199.220"
#define CAM_STREAM_PORT    8003

static WiFiClient streamClient;
static unsigned long frame_cnt = 0;
static unsigned long drop_cnt = 0;
static unsigned long reconnect_cnt = 0;
static unsigned long last_diag_ms = 0;

void loop() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb || fb->len == 0) {
    if (fb) esp_camera_fb_return(fb);
    return;
  }

  // TCP 自动重连
  if (!streamClient.connected()) {
    reconnect_cnt++;
    bool ok = streamClient.connect(CAM_STREAM_HOST, CAM_STREAM_PORT);
    Serial.printf("[TCP] 连接 %s (重连#%lu)\n", ok ? "成功" : "失败", reconnect_cnt);
    if (ok) streamClient.setNoDelay(true);
  }

  // 推帧并检查写入结果
  if (streamClient.connected()) {
    uint16_t len_be = htons((uint16_t)fb->len);
    size_t w1 = streamClient.write((uint8_t*)&len_be, 2);
    size_t w2 = streamClient.write(fb->buf, fb->len);
    streamClient.flush();  // ★ 必须 flush, 否则 lwIP 缓冲区满后帧堆积→黑屏
    frame_cnt++;

    if (w1 != 2 || w2 != fb->len) {
      drop_cnt++;
    }
  }

  esp_camera_fb_return(fb);

  // 每 3s 输出诊断 (不阻塞 loop)
  unsigned long now = millis();
  if (now - last_diag_ms > 3000) {
    last_diag_ms = now;
    float fps = frame_cnt / 3.0;
    Serial.printf("[DIAG] 帧=%lu | FPS≈%.1f | 丢=%lu | 重连=%lu | WiFi=%d | TCP=%d | JPEG=%uB\n",
                  frame_cnt, fps, drop_cnt, reconnect_cnt,
                  WiFi.status() == WL_CONNECTED,
                  streamClient.connected(),
                  fb->len);
    frame_cnt = 0;
    drop_cnt = 0;
  }
}
