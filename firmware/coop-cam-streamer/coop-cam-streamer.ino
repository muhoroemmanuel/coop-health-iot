// Coop-CAM streamer for AI-Thinker ESP32-CAM (OV2640).
// Phase 1 strategy: ESP32 streams MJPEG, laptop/RPi runs vision/camera_tracker.py.
// In Arduino IDE: Board "AI Thinker ESP32-CAM", Partition "Huge APP", PSRAM enabled.
#include "esp_camera.h"
#include <WiFi.h>

#define CAM_PIN_PWDN 32
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 0
#define CAM_PIN_SIOD 26
#define CAM_PIN_SIOC 27
#define CAM_PIN_D7 35
#define CAM_PIN_D6 34
#define CAM_PIN_D5 39
#define CAM_PIN_D4 36
#define CAM_PIN_D3 21
#define CAM_PIN_D2 19
#define CAM_PIN_D1 18
#define CAM_PIN_D0 5
#define CAM_PIN_VSYNC 25
#define CAM_PIN_HREF 23
#define CAM_PIN_PCLK 22

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

void startStreamServer();

void setup() {
  Serial.begin(115200);
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = CAM_PIN_D0; c.pin_d1 = CAM_PIN_D1; c.pin_d2 = CAM_PIN_D2;
  c.pin_d3 = CAM_PIN_D3; c.pin_d4 = CAM_PIN_D4; c.pin_d5 = CAM_PIN_D5;
  c.pin_d6 = CAM_PIN_D6; c.pin_d7 = CAM_PIN_D7; c.pin_xclk = CAM_PIN_XCLK;
  c.pin_pclk = CAM_PIN_PCLK; c.pin_vsync = CAM_PIN_VSYNC; c.pin_href = CAM_PIN_HREF;
  c.pin_sccb_sda = CAM_PIN_SIOD; c.pin_sccb_scl = CAM_PIN_SIOC;
  c.pin_pwdn = CAM_PIN_PWDN; c.pin_reset = CAM_PIN_RESET;
  c.xclk_freq_hz = 20000000; c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_SVGA; c.jpeg_quality = 12; c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM; c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) { Serial.println("Camera init failed"); return; }
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\nStream at http://%s:81/stream\n", WiFi.localIP().toString().c_str());
  startStreamServer();  // use ESP32 CameraWebServer example's app_httpd.cpp
}
void loop() { delay(10000); }
void startStreamServer() {}
