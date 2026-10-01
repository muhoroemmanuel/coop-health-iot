#pragma once

// ============================================================================
// NETWORK & MQTT CONFIGURATION TEMPLATE
// Copy this file to secrets.h and enter your actual Wi-Fi and MQTT credentials.
// ============================================================================

// Unique Node Identifier (used in MQTT topics: iot/devices/<DEVICE_ID>/...)
#define DEVICE_ID           "coop-health-01"

// Wi-Fi Access Point Credentials
#define WIFI_SSID           "YOUR_WIFI_SSID"
#define WIFI_PASSWORD       "YOUR_WIFI_PASSWORD"

// MQTT Broker Connection Settings
// Public test broker (unencrypted for quick verification): broker.hivemq.com port 1883
// For production / local coop deployment: use your Raspberry Pi / PC local IP (e.g., 192.168.1.50) port 1883
#define MQTT_BROKER         "broker.hivemq.com"
#define MQTT_PORT           1883

// Set to true if connecting to MQTT over TLS (port 8883)
#define MQTT_USE_TLS        false

// MQTT Authentication (leave empty strings if broker does not require authentication)
#define MQTT_USERNAME       ""
#define MQTT_PASSWORD       ""
