#pragma once

#include <Arduino.h>

// ============================================================================
// HARDWARE PIN ASSIGNMENTS (ESP32-S3)
// ============================================================================

// I2C Bus for SHT31 (0x44) and MLX90614 (0x5A)
#define PIN_I2C_SDA         8
#define PIN_I2C_SCL         9

// I2S Bus for INMP441 Omnidirectional MEMS Microphone
// INMP441 L/R pin wired to GND selects Left channel
#define PIN_I2S_BCLK        14   // Bit Clock (SCK)
#define PIN_I2S_WS          15   // Word Select / Left-Right Clock (WS)
#define PIN_I2S_DIN         16   // Serial Data In (SD)

// Digital I/O
#define PIN_PIR_MOTION      4    // HC-SR501 digital output (Active HIGH)
#define PIN_ALERT_OUTPUT    2    // LED indicator or active buzzer for local alerts

// Analog Inputs
#define PIN_BATTERY_ADC     1    // Optional battery voltage divider (ADC1_CH0)

// ============================================================================
// TIMING & SAMPLING CONSTANTS
// ============================================================================

// MQTT Telemetry publish interval (milliseconds)
#define TELEMETRY_INTERVAL_MS   10000UL   // 10 seconds

// MQTT Retained status heartbeat interval (milliseconds)
#define HEARTBEAT_INTERVAL_MS   60000UL   // 60 seconds

// Rolling 10-Minute Window Configuration
// 10 minutes = 600 seconds. We use 120 buckets of 5 seconds each.
#define ROLLING_BUCKET_SEC      5
#define ROLLING_BUCKET_COUNT    120      // 120 * 5s = 600s (10 minutes)

// Microphone / Acoustic Sampling Configuration
#define I2S_SAMPLE_RATE         16000    // 16 kHz standard for bird bioacoustics
#define I2S_DMA_BUF_LEN         512      // Samples per DMA buffer
#define I2S_DMA_BUF_COUNT       4

// Firmware metadata
#define FIRMWARE_VERSION        "1.0.0"
