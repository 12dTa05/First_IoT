/**
 * ESP8266 Relay Fan Controller
 * Features:
 * - Manual and automatic control modes
 * - Temperature-based automation
 * - TLS/SSL connection
 * - Status reporting
 * - Smooth transitions
 */

#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include <Ticker.h>

// ========== Configuration ==========
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

const char* MQTT_HOST = "192.168.1.148";
const uint16_t MQTT_PORT = 1884;

const char* DEVICE_ID = "fan_01";

// Topics
const char* TOPIC_COMMAND = "home/devices/fan_01/command";
const char* TOPIC_STATUS = "home/devices/fan_01/status";
const char* TOPIC_TELEMETRY = "home/devices/fan_01/telemetry";

// Certificate - ADD YOUR CA CERT HERE
const char ROOT_CA_PEM[] = R"EOF(
-----BEGIN CERTIFICATE-----
[YOUR CA CERTIFICATE HERE]
-----END CERTIFICATE-----
)EOF";

// ========== Hardware Configuration ==========
#define RELAY_PIN D1
#define LED_PIN D4  // Built-in LED (inverted)
#define STATUS_LED D2  // Optional external LED

BearSSL::X509List cert(ROOT_CA_PEM);
WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);
Ticker watchdogTimer;

// ========== State Variables ==========
bool fanState = false;
bool autoMode = true;
float tempThreshold = 28.0;
float currentTemperature = 0.0;

// Timing
unsigned long lastStatusUpdate = 0;
unsigned long lastReconnect = 0;

const unsigned long STATUS_INTERVAL = 60000;     // 1 minute
const unsigned long RECONNECT_INTERVAL = 5000;   // 5 seconds
const unsigned long WATCHDOG_TIMEOUT = 60000;    // 60 seconds

volatile bool watchdogFed = false;

// ========== Helper Functions ==========

void feedWatchdog() {
  watchdogFed = true;
}

void watchdogCheck() {
  if (!watchdogFed) {
    Serial.println("[WATCHDOG] System hung, rebooting...");
    ESP.restart();
  }
  watchdogFed = false;
}

void setFanState(bool state, const char* source) {
  /**
   * Set fan state with logging
   */
  if (fanState == state) {
    return;  // No change needed
  }
  
  fanState = state;
  
  // Update hardware
  digitalWrite(RELAY_PIN, state ? HIGH : LOW);
  digitalWrite(LED_PIN, state ? LOW : HIGH);  // Inverted for built-in LED
  digitalWrite(STATUS_LED, state ? HIGH : LOW);
  
  Serial.print("[FAN] ");
  Serial.print(state ? "ON" : "OFF");
  Serial.print(" (source: ");
  Serial.print(source);
  Serial.println(")");
  
  // Send status update
  publishStatus(source);
}

void publishStatus(const char* trigger) {
  /**
   * Publish device status
   */
  StaticJsonDocument<256> doc;
  doc["device_id"] = DEVICE_ID;
  doc["state"] = fanState ? "on" : "off";
  doc["auto_mode"] = autoMode;
  doc["temp_threshold"] = tempThreshold;
  doc["current_temp"] = currentTemperature;
  doc["trigger"] = trigger;
  doc["timestamp"] = time(nullptr);
  
  String payload;
  serializeJson(doc, payload);
  
  if (mqtt.publish(TOPIC_STATUS, payload.c_str())) {
    Serial.println("[STATUS] Published");
  }
}

void handleCommand(const JsonDocument &doc) {
  /**
   * Handle MQTT command
   */
  const char* cmd = doc["cmd"];
  if (!cmd) {
    Serial.println("[ERROR] No 'cmd' field in command");
    return;
  }
  
  Serial.print("[COMMAND] Received: ");
  Serial.println(cmd);
  
  if (strcmp(cmd, "fan_on") == 0) {
    // Manual ON
    autoMode = false;
    setFanState(true, "manual");
    
  } else if (strcmp(cmd, "fan_off") == 0) {
    // Manual OFF
    autoMode = false;
    setFanState(false, "manual");
    
  } else if (strcmp(cmd, "fan_toggle") == 0) {
    // Toggle
    autoMode = false;
    setFanState(!fanState, "manual");
    
  } else if (strcmp(cmd, "set_auto") == 0) {
    // Configure auto mode
    bool enable = doc["enable"] | false;
    autoMode = enable;
    
    if (doc.containsKey("threshold")) {
      tempThreshold = doc["threshold"];
    }
    
    Serial.print("[AUTO] Mode ");
    Serial.print(autoMode ? "ENABLED" : "DISABLED");
    Serial.print(", threshold: ");
    Serial.print(tempThreshold);
    Serial.println("C");
    
    // Re-evaluate if in auto mode
    if (autoMode && currentTemperature > 0) {
      bool shouldBeOn = (currentTemperature >= tempThreshold);
      setFanState(shouldBeOn, "auto");
    }
    
    publishStatus("config");
    
  } else if (strcmp(cmd, "temp_update") == 0) {
    // Temperature update for auto mode
    if (doc.containsKey("temperature")) {
      currentTemperature = doc["temperature"];
      
      Serial.print("[TEMP] Update: ");
      Serial.print(currentTemperature);
      Serial.println("C");
      
      if (autoMode) {
        bool shouldBeOn = (currentTemperature >= tempThreshold);
        setFanState(shouldBeOn, "auto");
      }
    }
    
  } else {
    Serial.print("[WARNING] Unknown command: ");
    Serial.println(cmd);
  }
}

// ========== MQTT Callbacks ==========

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  /**
   * Handle MQTT messages
   */
  String msg;
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  
  Serial.print("[MQTT] << ");
  Serial.print(topic);
  Serial.print(": ");
  Serial.println(msg);
  
  if (String(topic) == TOPIC_COMMAND) {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, msg);
    
    if (err) {
      Serial.print("[ERROR] JSON parse failed: ");
      Serial.println(err.c_str());
      return;
    }
    
    handleCommand(doc);
  }
}

void reconnectMQTT() {
  /**
   * Reconnect to MQTT broker
   */
  if (!mqtt.connected() && millis() - lastReconnect > RECONNECT_INTERVAL) {
    lastReconnect = millis();
    
    Serial.print("[MQTT] Connecting...");
    
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println(" connected");
      
      // Subscribe to command topic
      mqtt.subscribe(TOPIC_COMMAND);
      Serial.print("[MQTT] Subscribed to: ");
      Serial.println(TOPIC_COMMAND);
      
      // Publish online status
      StaticJsonDocument<128> doc;
      doc["device_id"] = DEVICE_ID;
      doc["state"] = "online";
      doc["timestamp"] = time(nullptr);
      
      String payload;
      serializeJson(doc, payload);
      mqtt.publish(TOPIC_STATUS, payload.c_str());
      
      // Send current fan status
      publishStatus("reconnect");
      
    } else {
      Serial.print(" failed, rc=");
      Serial.println(mqtt.state());
    }
  }
}

// ========== Setup ==========

void setup() {
  Serial.begin(115200);
  Serial.println("\n\n=== Fan Controller ===");
  Serial.print("Device ID: ");
  Serial.println(DEVICE_ID);
  
  // Initialize hardware
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);
  
  digitalWrite(RELAY_PIN, LOW);   // Fan OFF
  digitalWrite(LED_PIN, HIGH);    // Built-in LED OFF (inverted)
  digitalWrite(STATUS_LED, LOW);   // Status LED OFF
  
  // Connect WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] Connecting");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    digitalWrite(STATUS_LED, !digitalRead(STATUS_LED));  // Blink while connecting
    attempts++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[ERROR] WiFi connection failed!");
    ESP.restart();
  }
  
  Serial.println("\n[WiFi] Connected");
  Serial.print("[WiFi] IP: ");
  Serial.println(WiFi.localIP());
  digitalWrite(STATUS_LED, HIGH);
  
  // Setup NTP
  configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("[NTP] Syncing time");
  
  time_t now = time(nullptr);
  attempts = 0;
  while (now < 1600000000 && attempts < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    attempts++;
  }
  
  if (now < 1600000000) {
    Serial.println("\n[WARNING] Time sync failed!");
  } else {
    Serial.print("\n[NTP] Time synced: ");
    Serial.println(now);
  }
  
  // Setup TLS
  tlsClient.setTrustAnchors(&cert);
  // IMPORTANT: Remove setInsecure() for production!
  // tlsClient.setInsecure();
  
  // Setup MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  
  // Initial connection
  reconnectMQTT();
  
  // Start watchdog
  watchdogTimer.attach(WATCHDOG_TIMEOUT / 1000, watchdogCheck);
  
  Serial.println("[READY] System initialized");
  Serial.print("[HEAP] Free: ");
  Serial.println(ESP.getFreeHeap());
}

// ========== Main Loop ==========

void loop() {
  // Feed watchdog
  feedWatchdog();
  
  // Maintain MQTT connection
  if (!mqtt.connected()) {
    reconnectMQTT();
  }
  mqtt.loop();
  
  unsigned long currentMillis = millis();
  
  // Periodic status update
  if (currentMillis - lastStatusUpdate >= STATUS_INTERVAL) {
    lastStatusUpdate = currentMillis;
    publishStatus("periodic");
  }
  
  // Monitor heap memory
  static unsigned long lastHeapCheck = 0;
  if (currentMillis - lastHeapCheck > 60000) {
    lastHeapCheck = currentMillis;
    Serial.print("[HEAP] Free: ");
    Serial.println(ESP.getFreeHeap());
    
    if (ESP.getFreeHeap() < 10000) {
      Serial.println("[WARNING] Low memory");
    }
  }
  
  delay(10);
}