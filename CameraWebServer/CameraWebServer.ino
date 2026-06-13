#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>

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

  // ★ QVGA 320x240: 延迟最低, 够二维码/条码识别
  // ★ fb_count=2: 连续 I2S DMA 模式，esp_camera_fb_get() 直接从队列取帧
  // ★ jpeg_quality=12: ESP32-CAM 默认画质，二维码/条码识别必需清晰边界
  //    (QVGA@q12 ≈ 15-25KB, WiFi 传输 <50ms; q35 虽小但块状伪影导致识别失败)
  if (psramFound()) {
    config.frame_size = FRAMESIZE_HVGA;    // 320x240 → 延迟最低
    config.jpeg_quality = 12;              // 默认画质，识别更可靠
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

void loop() {
  // Do nothing. Everything is done in another task by the web server
  delay(10000);
}
