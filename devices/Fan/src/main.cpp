#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

const char* ssid = "Firewall_OWWRT";
const char* wifiPass = "12052003A";

const char* mqtt_host = "192.168.1.209";  
const uint16_t mqtt_port = 1884; 

const char* device_id = "fan_01";
const char* topic_command = "home/devices/fan_01/command";
const char* topic_status = "home/devices/fan_01/status";
const char* topic_telemetry = "home/devices/fan_01/telemetry";

const char* mqtt_username = "fan_01";
const char* mqtt_password = "125";

// ===== THÊM ROOT CA CERTIFICATE VÀO ĐÂY =====
const char root_ca_pem[] = R"EOF(
-----BEGIN CERTIFICATE-----
MIIC2TCCAcGgAwIBAgIURBwcLQMhYPwVf4jVmzA1IFcGCyMwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJTXlMb2NhbENBMB4XDTI1MTAxMjEwNDc1NFoXDTM1MTAx
MDEwNTI1NFowFDESMBAGA1UEAwwJTXlMb2NhbENBMIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEA3MKJIZKTCDh+wTO0WxoRFeTSl4/ee45VX5D8qDiqrRSc
JfQskDtIM0enNaZHqUdh5GXz25a8IJ7jBoiBskunxbp8nQm7ojKmWvv+5Y8sGGG+
nk5+Rf/DqtUr/0Ua/4aMN5vKBxhynNw5PE3DOTHb+aq2Pqgt9a0jwPIC0F6IxejK
Q1+EtmureFKnV1RKsfZEoWzUtRMx6fAiUJUVzZJFUinJNrKjYm8MsYQm1Wc+FwOz
fwH1lnYXSF8vtWsPD4uOC29gdKq3MhpFUYO0unPzglM0NYZCy+AUdg1MvLp+rrte
FGiFedtFQc6Dg7gCGjdeAXUeZkNR7s5+cKDS9WOzCQIDAQABoyMwITAPBgNVHRMB
Af8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsFAAOCAQEAQFgV
AzgP9cEBRkQIcUeIZK+Vgp6wPcFbCnjjAwfaZ1wmA67PEeeCLUPwMSCKfA8/YUdi
KkMahWl0sI43FmrWCo1XUz6rZtJ4oGmj88ACOpL5MSFflQOhUorx2sT2+8YYXIEU
EhF8bTBZWbSwkxHvP5KPrV8tQkfo/GWCRyE5e4YqCfXnMn0JmG5t/JYewN77K+Nf
TOAaPq+B2292lJviknA6470ZdHqXz+FTT0BtwYHBAfnPobhHrNO8DaR+etb1A6EE
OwzufsTJw/D+9FP0hoVWtMU341tWb93hg4TvZkzikS3QJHYnIkDmO5mtSudKDe8V
e4//OtMTZdTs/nuDdg==
-----END CERTIFICATE-----
)EOF";

// Hardware pins
#define RELAY_PIN D1
#define LED_PIN D4  // Built-in LED

BearSSL::X509List cert(root_ca_pem);
WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);

// Fan state
bool fanState = false;
bool autoMode = true;
float tempThreshold = 28.0;
float lastTemperature = 0.0;

// Timing
unsigned long lastStatusUpdate = 0;
const unsigned long statusInterval = 60000; // 1 minute
unsigned long lastReconnectAttempt = 0;
const unsigned long reconnectInterval = 5000;
unsigned long lastHeartbeat = 0;
const unsigned long heartbeatInterval = 300000; // 5 minutes

// Memory monitoring
unsigned long lastMemCheck = 0;
const unsigned long memCheckInterval = 60000;
const uint32_t minFreeHeap = 8000;

// Command acknowledgment
struct PendingCommand {
  String commandId;
  unsigned long timestamp;
  bool acknowledged;
};
PendingCommand pendingCmd = {"", 0, true};

void checkMemory() {
  uint32_t freeHeap = ESP.getFreeHeap();
  
  if (freeHeap < minFreeHeap) {
    Serial.printf("[WARNING] Low memory: %u bytes\n", freeHeap);
    
    if (mqtt.connected()) {
      StaticJsonDocument<128> alert;
      alert["device_id"] = device_id;
      alert["state"] = "low_memory";
      alert["free_heap"] = freeHeap;
      alert["timestamp"] = time(nullptr);
      
      String out;
      serializeJson(alert, out);
      mqtt.publish(topic_status, out.c_str(), true);
    }
    
    if (freeHeap < 4000) {
      Serial.println("[CRITICAL] Restarting due to low memory");
      delay(1000);
      ESP.restart();
    }
  }
}

void sendStatus(const char* trigger) {
  StaticJsonDocument<256> doc;
  doc["device_id"] = device_id;
  doc["state"] = fanState ? "on" : "off";
  doc["auto_mode"] = autoMode;
  doc["temp_threshold"] = tempThreshold;
  doc["last_temperature"] = lastTemperature;
  doc["trigger"] = trigger;
  doc["timestamp"] = time(nullptr);
  doc["free_heap"] = ESP.getFreeHeap();
  doc["wifi_rssi"] = WiFi.RSSI();
  
  String payload;
  serializeJson(doc, payload);
  
  if (mqtt.publish(topic_status, payload.c_str(), true)) {
    Serial.printf("[STATUS] Sent: %s\n", trigger);
  } else {
    Serial.println("[ERROR] Failed to send status");
  }
}

void sendCommandAck(const String& commandId, bool success, const char* error = nullptr) {
  StaticJsonDocument<200> doc;
  doc["device_id"] = device_id;
  doc["command_id"] = commandId;
  doc["success"] = success;
  doc["timestamp"] = time(nullptr);
  
  if (error) {
    doc["error"] = error;
  }
  
  String payload;
  serializeJson(doc, payload);
  
  // Publish acknowledgment to a dedicated ack topic
  String ackTopic = String(topic_status) + "/ack";
  mqtt.publish(ackTopic.c_str(), payload.c_str(), false);
  
  Serial.printf("[ACK] Command %s: %s\n", commandId.c_str(), success ? "success" : "failed");
}

void setFanState(bool state, const char* source) {
  bool previousState = fanState;
  fanState = state;
  
  digitalWrite(RELAY_PIN, state ? HIGH : LOW);
  digitalWrite(LED_PIN, state ? LOW : HIGH); // Inverted for built-in LED
  
  Serial.printf("[FAN] %s (source: %s)\n", state ? "ON" : "OFF", source);
  
  // Only send status if state actually changed
  if (previousState != fanState) {
    sendStatus(source);
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  
  Serial.printf("[MQTT] Received on %s: %s\n", topic, msg.c_str());
  
  if (String(topic) == topic_command) {
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, msg);
    
    if (err) {
      Serial.printf("[ERROR] JSON parse failed: %s\n", err.c_str());
      return;
    }
    
    const char* cmd = doc["cmd"];
    if (cmd == nullptr) {
      Serial.println("[ERROR] No 'cmd' field");
      return;
    }
    
    // Extract command ID for acknowledgment
    String commandId = doc["command_id"] | String(millis());
    
    // Manual control commands
    if (strcmp(cmd, "fan_on") == 0) {
      autoMode = false;
      setFanState(true, "manual");
      sendCommandAck(commandId, true);
      
    } else if (strcmp(cmd, "fan_off") == 0) {
      autoMode = false;
      setFanState(false, "manual");
      sendCommandAck(commandId, true);
      
    } else if (strcmp(cmd, "fan_toggle") == 0) {
      autoMode = false;
      setFanState(!fanState, "manual");
      sendCommandAck(commandId, true);
      
    // Auto mode configuration
    } else if (strcmp(cmd, "set_auto") == 0) {
      bool enable = doc["enable"] | false;
      autoMode = enable;
      
      if (doc.containsKey("threshold")) {
        float newThreshold = doc["threshold"];
        if (newThreshold >= 15.0 && newThreshold <= 50.0) {
          tempThreshold = newThreshold;
        } else {
          Serial.println("[ERROR] Invalid threshold value");
          sendCommandAck(commandId, false, "invalid_threshold");
          return;
        }
      }
      
      Serial.printf("[CONFIG] Auto mode: %s, threshold: %.1f°C\n", 
                    autoMode ? "ON" : "OFF", tempThreshold);
      
      sendStatus("config");
      sendCommandAck(commandId, true);
      
    // Temperature update (for auto mode)
    } else if (strcmp(cmd, "temp_update") == 0) {
      if (autoMode) {
        float temp = doc["temperature"];
        
        if (!isnan(temp) && temp >= -50.0 && temp <= 100.0) {
          lastTemperature = temp;
          bool shouldBeOn = (temp >= tempThreshold);
          
          if (shouldBeOn != fanState) {
            setFanState(shouldBeOn, "auto");
            Serial.printf("[AUTO] Temperature %.1f°C → Fan %s\n", 
                         temp, shouldBeOn ? "ON" : "OFF");
          }
          sendCommandAck(commandId, true);
        } else {
          Serial.println("[ERROR] Invalid temperature value");
          sendCommandAck(commandId, false, "invalid_temperature");
        }
      }
      
    // Status request
    } else if (strcmp(cmd, "status_request") == 0) {
      sendStatus("requested");
      sendCommandAck(commandId, true);
      
    } else {
      Serial.printf("[ERROR] Unknown command: %s\n", cmd);
      sendCommandAck(commandId, false, "unknown_command");
    }
  }
}

void reconnectMQTT() {
  if (millis() - lastReconnectAttempt < reconnectInterval) {
    return;
  }
  
  lastReconnectAttempt = millis();
  
  if (mqtt.connected()) {
    return;
  }
  
  Serial.print("[MQTT] Connecting...");
  
  if (mqtt.connect(device_id, mqtt_username, mqtt_password)) {
    Serial.println(" connected");
    
    mqtt.subscribe(topic_command, 1); // QoS 1
    Serial.printf("[MQTT] Subscribed to: %s\n", topic_command);
    
    // Send online status with retained flag
    StaticJsonDocument<128> st;
    st["device_id"] = device_id;
    st["state"] = "online";
    st["timestamp"] = time(nullptr);
    st["free_heap"] = ESP.getFreeHeap();
    String out;
    serializeJson(st, out);
    mqtt.publish(topic_status, out.c_str(), true);
    
    // Send current status
    sendStatus("reconnect");
    
  } else {
    Serial.printf(" failed, rc=%d\n", mqtt.state());
  }
}

void sendHeartbeat() {
  StaticJsonDocument<128> doc;
  doc["device_id"] = device_id;
  doc["type"] = "heartbeat";
  doc["state"] = fanState ? "on" : "off";
  doc["auto_mode"] = autoMode;
  doc["uptime"] = millis() / 1000;
  doc["free_heap"] = ESP.getFreeHeap();
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["timestamp"] = time(nullptr);
  
  String payload;
  serializeJson(doc, payload);
  
  mqtt.publish(topic_telemetry, payload.c_str(), false);
  Serial.println("[HEARTBEAT] Sent");
}

void setup() {
  Serial.begin(115200);
  delay(100);
  
  Serial.println("\n\n=================================");
  Serial.println("Fan Controller Starting");
  Serial.println("=================================");
  Serial.printf("Device ID: %s\n", device_id);
  Serial.printf("Free heap: %u bytes\n", ESP.getFreeHeap());
  
  // Initialize pins
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
  digitalWrite(LED_PIN, HIGH); // LED off
  
  // Connect WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, wifiPass);
  Serial.print("[WiFi] Connecting");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[ERROR] WiFi connection failed, restarting...");
    delay(1000);
    ESP.restart();
  }
  
  Serial.println("\n[WiFi] Connected");
  Serial.printf("[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("[WiFi] Signal: %d dBm\n", WiFi.RSSI());
  
  // Setup NTP
  configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");
  Serial.println("[NTP] Waiting for time sync...");
  
  time_t now = time(nullptr);
  int timeAttempts = 0;
  while (now < 1600000000 && timeAttempts < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    timeAttempts++;
  }
  
  if (now < 1600000000) {
    Serial.println("\n[WARNING] Time sync failed");
  } else {
    Serial.println();
    Serial.printf("[NTP] Time synced: %ld\n", now);
  }

  // Setup TLS - PROPERLY verify certificate
  tlsClient.setTrustAnchors(&cert);
  tlsClient.setInsecure();
  
  Serial.println("[TLS] Certificate verification: ENABLED");
  
  // Setup MQTT
  mqtt.setServer(mqtt_host, mqtt_port);
  mqtt.setCallback(mqttCallback);
  mqtt.setKeepAlive(60);
  
  // Initial connection
  reconnectMQTT();
  
  // Initialize default values
  autoMode = true;
  tempThreshold = 28.0;
  
  Serial.println("\n[SYSTEM] Ready!");
  Serial.println("=================================\n");
}

void loop() {
  // Maintain MQTT connection
  if (!mqtt.connected()) {
    reconnectMQTT();
  }
  mqtt.loop();
  
  unsigned long currentMillis = millis();
  
  // Memory check
  if (currentMillis - lastMemCheck >= memCheckInterval) {
    lastMemCheck = currentMillis;
    checkMemory();
  }
  
  // Periodic status update
  if (currentMillis - lastStatusUpdate >= statusInterval) {
    lastStatusUpdate = currentMillis;
    sendStatus("periodic");
  }
  
  // Heartbeat
  if (currentMillis - lastHeartbeat >= heartbeatInterval) {
    lastHeartbeat = currentMillis;
    sendHeartbeat();
  }
  
  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WARNING] WiFi disconnected, reconnecting...");
    WiFi.reconnect();
    delay(1000);
  }
}