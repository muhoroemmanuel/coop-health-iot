#include "sensors.h"
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_MLX90614.h>
#include <driver/i2s.h>
#include <math.h>

// Hardware sensor drivers
static Adafruit_SHT31 sht31 = Adafruit_SHT31();
static Adafruit_MLX90614 mlx = Adafruit_MLX90614();

static bool sht31_initialized = false;
static bool mlx_initialized = false;
static bool i2s_initialized = false;

// Alert state management
static volatile bool alert_active = false;
static volatile uint32_t alert_until_ms = 0;
static volatile int identify_pulses_remaining = 0;
static volatile uint32_t next_identify_toggle_ms = 0;
static volatile bool identify_led_level = false;

// Rolling window structures (10 minutes = 120 buckets x 5s)
static uint16_t cough_buckets[ROLLING_BUCKET_COUNT] = {0};
static uint16_t motion_buckets[ROLLING_BUCKET_COUNT] = {0};
static uint8_t current_bucket_idx = 0;
static uint32_t last_bucket_advance_ms = 0;

// Instantaneous acoustic indicators
static float last_calculated_db = 40.0f;
static float last_cough_confidence = 0.0f;

// PIR previous state for edge/pulse detection
static int prev_pir_state = LOW;

// Audio DMA buffer for I2S read
static int32_t i2s_raw_buffer[I2S_DMA_BUF_LEN];

void setAlertOutput(bool active) {
    alert_active = active;
    digitalWrite(PIN_ALERT_OUTPUT, active ? HIGH : LOW);
}

void triggerAlertPulse(uint32_t duration_ms) {
    if (duration_ms == 0) duration_ms = 3000;
    alert_until_ms = millis() + duration_ms;
    setAlertOutput(true);
}

void triggerIdentifyBlink() {
    identify_pulses_remaining = 10;
    identify_led_level = true;
    digitalWrite(PIN_ALERT_OUTPUT, HIGH);
    next_identify_toggle_ms = millis() + 150;
}

void updateAlertState() {
    uint32_t now = millis();
    if (identify_pulses_remaining > 0) {
        if (now >= next_identify_toggle_ms) {
            identify_pulses_remaining--;
            identify_led_level = !identify_led_level;
            digitalWrite(PIN_ALERT_OUTPUT, identify_led_level ? HIGH : LOW);
            next_identify_toggle_ms = now + 150;
        }
        return;
    }
    if (alert_until_ms > 0) {
        if (now >= alert_until_ms) {
            alert_until_ms = 0;
            setAlertOutput(false);
        }
    }
bool cough_classifier_process(const int16_t* samples, size_t sample_count, float* out_confidence) {
    if (sample_count == 0 || samples == NULL) {
        if (out_confidence) *out_confidence = 0.0f;
        return false;
    }

    int32_t peak = 0;
    int64_t sum_squares = 0;
    int zero_crossings = 0;
    int16_t prev_s = samples[0];

    for (size_t i = 0; i < sample_count; i++) {
        int16_t s = samples[i];
        int32_t abs_s = (s < 0) ? -s : s;
        if (abs_s > peak) peak = abs_s;
        sum_squares += (int64_t)s * s;

        if ((prev_s < 0 && s >= 0) || (prev_s >= 0 && s < 0)) {
            zero_crossings++;
        }
        prev_s = s;
    }

    float rms = sqrtf((float)sum_squares / (float)sample_count);
    float crest_factor = (rms > 1.0f) ? ((float)peak / rms) : 0.0f;
    float approx_freq = ((float)zero_crossings * I2S_SAMPLE_RATE) / (2.0f * sample_count);

    bool is_cough = (rms > 2500.0f) && (crest_factor > 3.2f) && (approx_freq >= 1200.0f && approx_freq <= 4200.0f);

    if (is_cough) {
        float conf = 70.0f + (crest_factor - 3.2f) * 6.0f;
        if (conf > 98.0f) conf = 98.0f;
        if (out_confidence) *out_confidence = conf;
        return true;
    }

    if (out_confidence) *out_confidence = 0.0f;
    return false;
}

static bool setupI2S() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = I2S_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = I2S_DMA_BUF_COUNT,
        .dma_buf_len = I2S_DMA_BUF_LEN,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = PIN_I2S_BCLK,
        .ws_io_num = PIN_I2S_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = PIN_I2S_DIN
    };

    esp_err_t err = i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("[I2S] Driver install failed: 0x%x\n", err);
        return false;
    }

    err = i2s_set_pin(I2S_NUM_0, &pin_config);
    if (err != ESP_OK) {
        Serial.printf("[I2S] Pin config failed: 0x%x\n", err);
        return false;
    }

    i2s_zero_dma_buffer(I2S_NUM_0);
    return true;
}

bool initSensors() {
    Serial.println("[Sensors] Initializing hardware peripherals...");

    pinMode(PIN_PIR_MOTION, INPUT);
    pinMode(PIN_ALERT_OUTPUT, OUTPUT);
    digitalWrite(PIN_ALERT_OUTPUT, LOW);

    pinMode(PIN_BATTERY_ADC, INPUT);
    analogReadResolution(12);

    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);

    if (sht31.begin(0x44)) {
        sht31_initialized = true;
        Serial.println("[Sensors] SHT31 (Temp/Humidity) connected at 0x44");
    } else {
        Serial.println("[Sensors] SHT31 NOT detected at 0x44 - verify wiring");
    }
void updateSensorsTick() {
    uint32_t now = millis();

    if (now - last_bucket_advance_ms >= (ROLLING_BUCKET_SEC * 1000UL)) {
        last_bucket_advance_ms = now;
        current_bucket_idx = (current_bucket_idx + 1) % ROLLING_BUCKET_COUNT;
        cough_buckets[current_bucket_idx] = 0;
        motion_buckets[current_bucket_idx] = 0;
    }

    int pir_val = digitalRead(PIN_PIR_MOTION);
    if (pir_val == HIGH && prev_pir_state == LOW) {
        motion_buckets[current_bucket_idx]++;
    }
    prev_pir_state = pir_val;

    if (i2s_initialized) {
        size_t bytes_read = 0;
        esp_err_t res = i2s_read(I2S_NUM_0, (void*)i2s_raw_buffer, sizeof(i2s_raw_buffer), &bytes_read, 0);
        if (res == ESP_OK && bytes_read > 0) {
            size_t sample_count = bytes_read / sizeof(int32_t);
            int16_t downsampled[I2S_DMA_BUF_LEN];
            int64_t sum_sq = 0;

            for (size_t i = 0; i < sample_count; i++) {
                int32_t raw24 = i2s_raw_buffer[i] >> 8;
                int16_t s16 = (int16_t)(raw24 >> 8);
                downsampled[i] = s16;
                sum_sq += (int64_t)s16 * s16;
            }

            if (sample_count > 0) {
                float rms = sqrtf((float)sum_sq / (float)sample_count);
                if (rms > 1.0f) {
                    float db = 20.0f * log10f(rms) - 10.0f;
                    if (db < 30.0f) db = 30.0f;
                    if (db > 110.0f) db = 110.0f;
                    last_calculated_db = (last_calculated_db * 0.85f) + (db * 0.15f);
                }

                float conf = 0.0f;
                if (cough_classifier_process(downsampled, sample_count, &conf)) {
                    cough_buckets[current_bucket_idx]++;
                    last_cough_confidence = conf;
                    Serial.printf("[Bioacoustics] Avian Cough Detected! Conf: %.1f%%, Total in 10m: %d\n",
                                  conf, cough_buckets[current_bucket_idx]);
                }
            }
        }
    }

    updateAlertState();
}

SensorData readSensors() {
    SensorData data;
    memset(&data, 0, sizeof(SensorData));

    data.sht31_ok = sht31_initialized;
    data.mlx_ok = mlx_initialized;
    data.i2s_ok = i2s_initialized;

    if (sht31_initialized) {
        float t = sht31.readTemperature();
        float h = sht31.readHumidity();
        if (!isnan(t) && !isnan(h)) {
            data.air_temperature_c = t;
            data.humidity_percent = h;
        } else {
            data.air_temperature_c = 24.0f;
            data.humidity_percent = 55.0f;
        }
    } else {
        data.air_temperature_c = 24.0f;
        data.humidity_percent = 55.0f;
    }

    if (mlx_initialized) {
        float obj_t = mlx.readObjectTempC();
        if (!isnan(obj_t) && obj_t > 0.0f && obj_t < 70.0f) {
            data.object_temperature_c = obj_t;
        } else {
            data.object_temperature_c = 39.5f;
        }
    } else {
        data.object_temperature_c = 39.5f;
    }

    data.sound_db = last_calculated_db;
    data.cough_confidence = last_cough_confidence;

    int total_coughs = 0;
    int total_motion = 0;
    for (int i = 0; i < ROLLING_BUCKET_COUNT; i++) {
        total_coughs += cough_buckets[i];
        total_motion += motion_buckets[i];
    }
    data.cough_count_10m = total_coughs;
    data.motion_events_10m = total_motion;

    int raw_adc = analogRead(PIN_BATTERY_ADC);
    float voltage = ((float)raw_adc / 4095.0f) * 3.3f * 2.0f;
    if (voltage >= 4.2f) {
        data.battery_percent = 100;
    } else if (voltage <= 3.2f) {
        data.battery_percent = 5;
    } else {
        data.battery_percent = (int)((voltage - 3.2f) / (4.2f - 3.2f) * 100.0f);
    }

    return data;
}


    if (mlx.begin(0x5A, &Wire)) {
        mlx_initialized = true;
        Serial.println("[Sensors] MLX90614 (IR Flock Temp) connected at 0x5A");
    } else {
        Serial.println("[Sensors] MLX90614 NOT detected at 0x5A - verify wiring");
    }

    if (setupI2S()) {
        i2s_initialized = true;
        Serial.println("[Sensors] INMP441 I2S microphone driver initialized (16 kHz)");
    } else {
        Serial.println("[Sensors] I2S initialization failed");
    }

    resetRollingWindows();
    return (sht31_initialized || mlx_initialized || i2s_initialized);
}

}

void resetRollingWindows() {
    for (int i = 0; i < ROLLING_BUCKET_COUNT; i++) {
        cough_buckets[i] = 0;
        motion_buckets[i] = 0;
    }
    current_bucket_idx = 0;
    last_bucket_advance_ms = millis();
    last_cough_confidence = 0.0f;
}
