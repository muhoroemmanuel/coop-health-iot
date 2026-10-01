#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

#include "config.h"
#include "secrets.h"
#include "sensors.h"

// Networking objects
static WiFiClient espWifiClient;
static WiFiClientSecure espTlsClient;
static PubSubClient mqttClient;

// Timing trackers
static unsigned long lastTelemetryPublishMs = 0;
static unsigned long lastHeartbeatPublishMs = 0;
static unsigned long bootTimeMs = 0;

// Topic strings constructed at runtime
static char topicTelemetry[80];
static char topicStatus[80];
static char topicCommands[80];
static char topicCommandsAck[80];

// NTP Time Configuration
static const char* ntpServer = "pool.ntp.org";
static const long gmtOffset_sec = 0;
static const int daylightOffset_sec = 0;

static String getIsoTimestamp() {
    time_t now;
    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 50)) {
        // Fallback if NTP sync hasn't occurred yet
        char buf[32];
        snprintf(buf, sizeof(buf), "1970-01-01T00:00:%02luZ", (millis() / 1000) % 60);
        return String(buf);
    }
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return String(buf);
}

static void publishCommandAck(const char* command, const char* status) {
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["command"] = command;
    doc["status"] = status;
    doc["timestamp"] = getIsoTimestamp();

    char payload[256];
    size_t len = serializeJson(doc, payload);
    mqttClient.publish(topicCommandsAck, payload, len);
    Serial.printf("[MQTT] Sent Ack -> %s: %s (%s)\n", topicCommandsAck, command, status);
}

static void handleIncomingCommand(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT] Inbound message on %s\n", topic);
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, payload, length);
    if (error) {
        Serial.printf("[MQTT] JSON parse error: %s\n", error.c_str());
        return;
    }

    const char* command = doc["command"] | "";
    if (strlen(command) == 0) {
        Serial.println("[MQTT] Command payload missing 'command' field");
        return;
    }

    if (strcmp(command, "notify_operator") == 0) {
        uint32_t duration_ms = doc["duration_ms"] | 3000UL;
        Serial.printf("[Action] notify_operator triggered for %u ms\n", duration_ms);
        triggerAlertPulse(duration_ms);
        publishCommandAck("notify_operator", "success");
    } else if (strcmp(command, "identify") == 0) {
        Serial.println("[Action] identify triggered - flashing LED");
        triggerIdentifyBlink();
        publishCommandAck("identify", "success");
    } else if (strcmp(command, "reset_baseline") == 0) {
        Serial.println("[Action] reset_baseline triggered - clearing accumulators");
        resetRollingWindows();
        publishCommandAck("reset_baseline", "success");
static void connectMqtt() {
    snprintf(topicTelemetry, sizeof(topicTelemetry), "iot/devices/%s/telemetry", DEVICE_ID);
    snprintf(topicStatus, sizeof(topicStatus), "iot/devices/%s/status", DEVICE_ID);
    snprintf(topicCommands, sizeof(topicCommands), "iot/devices/%s/commands", DEVICE_ID);
    snprintf(topicCommandsAck, sizeof(topicCommandsAck), "iot/devices/%s/commands/ack", DEVICE_ID);

    char lwtPayload[128];
    snprintf(lwtPayload, sizeof(lwtPayload), "{\"device_id\":\"%s\",\"state\":\"offline\",\"timestamp\":\"%s\"}",
             DEVICE_ID, getIsoTimestamp().c_str());

    String clientId = "coop-node-" + String(DEVICE_ID) + "-" + String(random(0xffff), HEX);

    Serial.printf("[MQTT] Connecting to %s:%d as %s...\n", MQTT_BROKER, MQTT_PORT, clientId.c_str());

    bool connected = false;
    if (strlen(MQTT_USERNAME) > 0) {
        connected = mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD,
                                       topicStatus, 1, true, lwtPayload);
    } else {
        connected = mqttClient.connect(clientId.c_str(), NULL, NULL,
                                       topicStatus, 1, true, lwtPayload);
    }

    if (connected) {
        Serial.println("[MQTT] Connected successfully!");

        // Publish online heartbeat retained
        JsonDocument statusDoc;
        statusDoc["device_id"] = DEVICE_ID;
        statusDoc["state"] = "online";
        statusDoc["firmware"] = FIRMWARE_VERSION;
        statusDoc["ip"] = WiFi.localIP().toString();
        statusDoc["rssi_dbm"] = WiFi.RSSI();
        statusDoc["battery_percent"] = readSensors().battery_percent;
        statusDoc["uptime_s"] = (millis() - bootTimeMs) / 1000UL;
        statusDoc["timestamp"] = getIsoTimestamp();

        char statusBuf[256];
        size_t sLen = serializeJson(statusDoc, statusBuf);
        mqttClient.publish(topicStatus, (const uint8_t*)statusBuf, sLen, true);
        Serial.printf("[MQTT] Retained status online published to %s\n", topicStatus);

        // Subscribe to remote commands
        mqttClient.subscribe(topicCommands, 1);
        Serial.printf("[MQTT] Subscribed to %s\n", topicCommands);
    } else {
        Serial.printf("[MQTT] Connect failed, rc=%d. Will retry...\n", mqttClient.state());
    }
}

static void publishTelemetry() {
    SensorData s = readSensors();

    JsonDocument doc;
    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = getIsoTimestamp();
    doc["uptime_s"] = (millis() - bootTimeMs) / 1000UL;

    JsonObject readings = doc["readings"].to<JsonObject>();
    readings["cough_count_10m"] = s.cough_count_10m;
    readings["cough_confidence"] = serialized(String(s.cough_confidence, 1));
    readings["sound_db"] = serialized(String(s.sound_db, 1));
    readings["object_temperature_c"] = serialized(String(s.object_temperature_c, 1));
    readings["air_temperature_c"] = serialized(String(s.air_temperature_c, 1));
    readings["humidity_percent"] = serialized(String(s.humidity_percent, 1));
    readings["motion_events_10m"] = s.motion_events_10m;

    doc["battery_percent"] = s.battery_percent;
    doc["rssi_dbm"] = WiFi.RSSI();

    char payload[512];
    size_t len = serializeJson(doc, payload);
    bool pubOk = mqttClient.publish(topicTelemetry, payload, len);
    if (pubOk) {
        Serial.printf("[Telemetry] Pub to %s (len=%u) | Coughs10m=%d, FlockTemp=%.1f C, AirTemp=%.1f C, Motion=%d\n",
                      topicTelemetry, len, s.cough_count_10m, s.object_temperature_c, s.air_temperature_c, s.motion_events_10m);
    } else {
        Serial.println("[Telemetry] Publish failed - buffer full or client disconnected");
static void publishHeartbeat() {
    JsonDocument statusDoc;
    statusDoc["device_id"] = DEVICE_ID;
    statusDoc["state"] = "online";
    statusDoc["firmware"] = FIRMWARE_VERSION;
    statusDoc["ip"] = WiFi.localIP().toString();
    statusDoc["rssi_dbm"] = WiFi.RSSI();
    statusDoc["battery_percent"] = readSensors().battery_percent;
    statusDoc["uptime_s"] = (millis() - bootTimeMs) / 1000UL;
    statusDoc["timestamp"] = getIsoTimestamp();

    char statusBuf[256];
    size_t sLen = serializeJson(statusDoc, statusBuf);
    mqttClient.publish(topicStatus, (const uint8_t*)statusBuf, sLen, true);
    Serial.printf("[Heartbeat] Retained status update published to %s\n", topicStatus);
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    bootTimeMs = millis();

    Serial.println("\n=============================================");
    Serial.println(" Newcastle Disease Early Warning Node (ESP32-S3)");
    Serial.printf(" Device ID: %s | Firmware: %s\n", DEVICE_ID, FIRMWARE_VERSION);
    Serial.println("=============================================");

    initSensors();

    Serial.printf("[WiFi] Connecting to %s...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 20) {
        delay(500);
        Serial.print(".");
        retries++;
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WiFi] Connected! IP: %s | RSSI: %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
        configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
    } else {
        Serial.println("[WiFi] Connection timeout - continuing in offline mode, will retry in loop");
    }

    if (MQTT_USE_TLS) {
        espTlsClient.setInsecure();
        mqttClient.setClient(espTlsClient);
    } else {
        mqttClient.setClient(espWifiClient);
    }

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(handleIncomingCommand);
    mqttClient.setBufferSize(1024);
}

void loop() {
    updateSensorsTick();

    if (WiFi.status() != WL_CONNECTED) {
        static unsigned long lastWifiRetry = 0;
        if (millis() - lastWifiRetry > 10000) {
            lastWifiRetry = millis();
            WiFi.reconnect();
        }
    } else {
        if (!mqttClient.connected()) {
            static unsigned long lastMqttRetry = 0;
            if (millis() - lastMqttRetry > 5000) {
                lastMqttRetry = millis();
                connectMqtt();
            }
        } else {
            mqttClient.loop();

            unsigned long now = millis();
            if (now - lastTelemetryPublishMs >= TELEMETRY_INTERVAL_MS) {
                lastTelemetryPublishMs = now;
                publishTelemetry();
            }

            if (now - lastHeartbeatPublishMs >= HEARTBEAT_INTERVAL_MS) {
                lastHeartbeatPublishMs = now;
                publishHeartbeat();
            }
        }
    }

    delay(2);
}

    }
}

    } else {
        Serial.printf("[Action] Unknown command received: %s\n", command);
        publishCommandAck(command, "unsupported");
    }
}
