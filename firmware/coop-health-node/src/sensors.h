#pragma once

#include <Arduino.h>
#include "config.h"

// Aggregated sensor readings structure matching PROTOCOL.md
struct SensorData {
    float air_temperature_c;      // From SHT31
    float humidity_percent;       // From SHT31
    float object_temperature_c;   // From MLX90614 (roosting birds IR surface temp)
    float sound_db;               // From INMP441 (RMS dB SPL estimated)
    int   cough_count_10m;        // Rolling 10-minute respiratory cough/sneeze count
    float cough_confidence;       // Confidence of detection (0-100%)
    int   motion_events_10m;      // Rolling 10-minute PIR motion pulse count
    int   battery_percent;        // Estimated battery % from ADC
    
    // Hardware health flags
    bool  sht31_ok;
    bool  mlx_ok;
    bool  i2s_ok;
};

// Initialize all hardware peripherals (I2C, I2S, PIR, Alert Pin)
bool initSensors();

// High-frequency task / loop pump: polls audio DMA buffer & PIR pin, updates rolling buckets
void updateSensorsTick();

// Collects instantaneous and 10-minute windowed readings for publishing
SensorData readSensors();

// Alert hardware control (LED/Buzzer)
void setAlertOutput(bool active);
void triggerAlertPulse(uint32_t duration_ms);
void updateAlertState();
void triggerIdentifyBlink();


// Reset baseline / window accumulators
void resetRollingWindows();

// Hook for future Edge Impulse TinyML model drop-in
// Takes raw PCM audio frame and returns true if classified as cough/sneeze
bool cough_classifier_process(const int16_t* samples, size_t sample_count, float* out_confidence);
