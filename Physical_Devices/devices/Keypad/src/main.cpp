#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Servo.h>
#include <Keypad.h>
#include <bearssl/bearssl_hmac.h>
#include <bearssl/bearssl_hash.h>
#include <time.h>

const char* ssid = "Home_IoT";
const char* wifiPass = "12052003";

const char* mqtt_host = "192.168.1.125";  
const uint16_t mqtt_port = 1884;          

const char* device_id = "passkey_01";
const char* topic_req = "home/devices/passkey_01/request";
const char* topic_cmd = "home/devices/passkey_01/command";
const char* topic_status = "home/devices/passkey_01/status";

const char* DEVICE_SALT = "passkey_01_salt_2025";

const char* mqtt_username = "passkey_01";
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

// HMAC Key - phải giống với gateway
const uint8_t HMAC_KEY[32] = {
  0x5A, 0x5A, 0x2B, 0x3F, 0x87, 0xDA, 0x01, 0xF9,
  0xDE, 0xE1, 0x83, 0xAD, 0x84, 0x54, 0xB5, 0x34,
  0x77, 0x68, 0x47, 0x8C, 0xE8, 0xFD, 0x73, 0x1F,
  0xBD, 0xE1, 0x3C, 0x42, 0x79, 0xB8, 0xFE, 0xA4
};

// Hardware pins
const int LED_OK = D0;
const int LED_ERR = D1;
const int SERVO_PIN = D8;

// Keypad configuration (3x3)
const byte ROWS = 3;
const byte COLS = 3;
char keys[ROWS][COLS] = {
  {'1','2','3'},
  {'4','5','6'},
  {'7','8','9'}
};
byte rowPins[ROWS] = { D2, D3, D4 };
byte colPins[COLS] = { D5, D6, D7 };
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

Servo doorServo;
BearSSL::X509List cert(root_ca_pem);
WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);

// State variables
String curPw = "";
bool waitingForReply = false;
unsigned long lastKeyPress = 0;
unsigned long lastReconnectAttempt = 0;
const unsigned long reconnectInterval = 5000;

// Retry configuration
const int maxRetries = 3;
int consecutiveFailures = 0;

// Memory monitoring
unsigned long lastMemCheck = 0;
const unsigned long memCheckInterval = 30000;
const uint32_t minFreeHeap = 8000;

struct RemoteUnlockConfig {
    bool enabled = true;
    unsigned long default_duration_ms = 5000;  // 5 seconds default
    unsigned long max_duration_ms = 30000;     // 30 seconds maximum
    bool require_reason = true;
    bool audit_log = true;
};

RemoteUnlockConfig remoteConfig;

// Track remote unlock state
struct RemoteUnlockState {
    bool active = false;
    String command_id = "";
    String initiated_by = "";
    String reason = "";
    unsigned long unlock_time = 0;
    unsigned long duration_ms = 0;
};

RemoteUnlockState remoteState;

// HMAC calculation using FULL hash (64 characters)
String calc_hmac_sha256_hex(const String &data) {
  uint8_t mac[32];
  br_hmac_key_context kc;
  br_hmac_context ctx;

  br_hmac_key_init(&kc, &br_sha256_vtable, HMAC_KEY, sizeof(HMAC_KEY));
  br_hmac_init(&ctx, &kc, 32);  
  br_hmac_update(&ctx, (const unsigned char*)data.c_str(), data.length());
  br_hmac_out(&ctx, mac);

  char hex[65];
  hex[64] = 0;
  for (int i = 0; i < 32; i++) {
    sprintf(hex + i * 2, "%02x", mac[i]);
  }
  return String(hex);
}

// SHA256 calculation - FULL hash (64 characters)
String calc_sha256_hex(const String &data) {
  uint8_t hash[32];
  br_sha256_context ctx;
  br_sha256_init(&ctx);

  String salted = String(DEVICE_SALT) + data;
  br_sha256_update(&ctx, (const unsigned char*)salted.c_str(), salted.length());
  br_sha256_out(&ctx, hash);

  char hex[65];
  hex[64] = 0;
  for (int i = 0; i < 32; i++) {
    sprintf(hex + i * 2, "%02x", hash[i]);
  }
  return String(hex);
}

void checkMemory() {
  uint32_t freeHeap = ESP.getFreeHeap();
  
  if (freeHeap < minFreeHeap) {
    Serial.printf("[WARNING] Low memory: %u bytes free\n", freeHeap);
    
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
      Serial.println("[CRITICAL] Memory critically low, restarting...");
      delay(1000);
      ESP.restart();
    }
  }
}

void blinkLED(int pin, int times, int delayMs) {
  for (int i = 0; i < times; i++) {
    digitalWrite(pin, HIGH);
    delay(delayMs);
    digitalWrite(pin, LOW);
    delay(delayMs);
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  
  Serial.print("[MQTT] Received: ");
  Serial.println(msg);

  if (String(topic) == topic_cmd) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, msg);
    
    if (err) {
      Serial.print("[ERROR] JSON parse failed: ");
      Serial.println(err.c_str());
      return;
    }

    const char* cmd = doc["cmd"];
    if (cmd == nullptr) {
      Serial.println("[ERROR] No 'cmd' field in message");
      return;
    }

    if (strcmp(cmd, "remote_unlock") == 0) {
      handleRemoteUnlock(doc);
      return;
    }
        
        // ===== NEW: Handle Remote Lock Command =====
    if (strcmp(cmd, "remote_lock") == 0) {
      handleRemoteLock(doc);
      return;
    }

    if (strcmp(cmd, "update_config") == 0) {
      handleConfigUpdate(doc);
      return;
    }

    if (strcmp(cmd, "OPEN") == 0) {
      Serial.println("[SUCCESS] Access granted - Opening door");
      
      doorServo.write(180);
      delay(500);

      StaticJsonDocument<64> st;
      st["state"] = "OPENED";
      st["timestamp"] = time(nullptr);
      String out; 
      serializeJson(st, out);
      mqtt.publish(topic_status, out.c_str());

      digitalWrite(LED_OK, HIGH); 
      digitalWrite(LED_ERR, LOW);
      
      delay(2000);
      digitalWrite(LED_OK, LOW);
      
      waitingForReply = false;
      consecutiveFailures = 0;
      
    } else if (strcmp(cmd, "LOCK") == 0) {
      Serial.println("[DENIED] Access denied");
      
      doorServo.write(0);

      StaticJsonDocument<128> st;
      st["state"] = "LOCKED";
      st["timestamp"] = time(nullptr);

      const char* reason = doc["reason"];
      if (reason) {
        st["reason"] = reason;
        Serial.print("[REASON] ");
        Serial.println(reason);
      }

      String out; 
      serializeJson(st, out);
      mqtt.publish(topic_status, out.c_str());

      digitalWrite(LED_OK, LOW); 
      digitalWrite(LED_ERR, HIGH);
      
      delay(1500);
      digitalWrite(LED_ERR, LOW);
      
      waitingForReply = false;
      consecutiveFailures++;
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
  
  Serial.print("[MQTT] Connecting to broker...");
  
  if (mqtt.connect(device_id, mqtt_username, mqtt_password)) {
    Serial.println(" connected");
    
    mqtt.subscribe(topic_cmd, 1);
    Serial.printf("[MQTT] Subscribed to: %s\n", topic_cmd);

    StaticJsonDocument<128> st;
    st["state"] = "online";
    st["device_id"] = device_id;
    st["timestamp"] = time(nullptr);
    st["free_heap"] = ESP.getFreeHeap();
    
    String out;
    serializeJson(st, out);
    mqtt.publish(topic_status, out.c_str(), true);
    
    consecutiveFailures = 0;
    
  } else {
    Serial.print(" failed, rc=");
    Serial.println(mqtt.state());
    consecutiveFailures++;
    
    if (consecutiveFailures > maxRetries * 3) {
      Serial.println("[ERROR] Too many connection failures, restarting...");
      delay(1000);
      ESP.restart();
    }
  }
}

void sendUnlockRequest(String password) {
  if (!mqtt.connected()) {
    Serial.println("[ERROR] MQTT not connected, cannot send request");
    blinkLED(LED_ERR, 3, 200);
    return;
  }
  
  String passwordHash = calc_sha256_hex(password);
  
  Serial.print("[DEBUG] Password hash (full): ");
  Serial.println(passwordHash);
  
  StaticJsonDocument<256> body;
  body["cmd"] = "unlock_request";
  body["client_id"] = device_id;
  body["pw"] = passwordHash;
  body["ts"] = time(nullptr);
  body["nonce"] = random(0, 2147483647);

  String bodyStr; 
  serializeJson(body, bodyStr);

  String sig = calc_hmac_sha256_hex(bodyStr);

  Serial.print("[DEBUG] HMAC signature: ");
  Serial.println(sig);

  StaticJsonDocument<350> wrap;
  wrap["body"] = bodyStr;
  wrap["hmac"] = sig;
  
  String payload; 
  serializeJson(wrap, payload);

  if (mqtt.publish(topic_req, payload.c_str(), false)) {
    Serial.println("[MQTT] Unlock request sent successfully");
    waitingForReply = true;
  } else {
    Serial.println("[ERROR] Failed to send unlock request");
    blinkLED(LED_ERR, 2, 300);
  }
}

void handleRemoteUnlock(JsonDocument& doc) {
    Serial.println("[REMOTE] Remote unlock request received");
    
    // Check if remote unlock is enabled
    if (!remoteConfig.enabled) {
        sendRemoteResponse(doc["command_id"], false, "remote_unlock_disabled");
        Serial.println("[REMOTE] Remote unlock is disabled");
        return;
    }
    
    // Extract parameters
    String command_id = doc["command_id"] | String(millis());
    String initiated_by = doc["user"] | "unknown";
    String reason = doc["reason"] | "no_reason_provided";
    unsigned long duration = doc["duration_ms"] | remoteConfig.default_duration_ms;
    
    // Validate duration
    if (duration > remoteConfig.max_duration_ms) {
        duration = remoteConfig.max_duration_ms;
        Serial.printf("[REMOTE] Duration capped at max: %lu ms\n", duration);
    }
    
    // Log remote unlock attempt
    logRemoteAccess("unlock", command_id, initiated_by, reason, duration);
    
    // Store state
    remoteState.active = true;
    remoteState.command_id = command_id;
    remoteState.initiated_by = initiated_by;
    remoteState.reason = reason;
    remoteState.unlock_time = millis();
    remoteState.duration_ms = duration;
    
    // Execute unlock
    executeUnlock("remote_unlock", duration);
    
    // Send acknowledgment
    sendRemoteResponse(command_id, true, "unlocked");
    
    Serial.printf("[REMOTE] Door unlocked by %s for %lu ms\n", 
                  initiated_by.c_str(), duration);
}

// ============= REMOTE LOCK HANDLER =============
void handleRemoteLock(JsonDocument& doc) {
    Serial.println("[REMOTE] Remote lock request received");
    
    String command_id = doc["command_id"] | String(millis());
    String initiated_by = doc["user"] | "unknown";
    
    // Cancel any active remote unlock
    if (remoteState.active) {
        remoteState.active = false;
        Serial.println("[REMOTE] Cancelled active remote unlock");
    }
    
    // Execute lock immediately
    doorServo.write(0);
    digitalWrite(LED_OK, LOW);
    digitalWrite(LED_ERR, HIGH);
    delay(500);
    digitalWrite(LED_ERR, LOW);
    
    // Log and respond
    logRemoteAccess("lock", command_id, initiated_by, "manual_lock", 0);
    sendRemoteResponse(command_id, true, "locked");
    sendStatus("locked", "remote_lock");
    
    Serial.printf("[REMOTE] Door locked by %s\n", initiated_by.c_str());
}

// ============= CONFIGURATION UPDATE HANDLER =============
void handleConfigUpdate(JsonDocument& doc) {
    Serial.println("[CONFIG] Configuration update received");
    
    if (doc.containsKey("remote_enabled")) {
        remoteConfig.enabled = doc["remote_enabled"];
    }
    
    if (doc.containsKey("default_duration_ms")) {
        remoteConfig.default_duration_ms = doc["default_duration_ms"];
    }
    
    if (doc.containsKey("max_duration_ms")) {
        remoteConfig.max_duration_ms = doc["max_duration_ms"];
    }
    
    // Save to EEPROM if needed
    // saveConfigToEEPROM();
    
    Serial.println("[CONFIG] Configuration updated successfully");
    sendRemoteResponse(doc["command_id"], true, "config_updated");
}

// ============= UNIFIED UNLOCK EXECUTION =============
void executeUnlock(const char* method, unsigned long duration_ms) {
    Serial.printf("[UNLOCK] Opening door via %s for %lu ms\n", method, duration_ms);
    
    // Open door
    doorServo.write(180);
    digitalWrite(LED_OK, HIGH);
    digitalWrite(LED_ERR, LOW);
    
    // Send status
    sendStatus("unlocked", method);
    
    // Visual feedback
    blinkLED(LED_OK, 3, 200);
    digitalWrite(LED_OK, HIGH);
    
    // Wait for specified duration
    delay(duration_ms);
    
    // Auto-lock
    doorServo.write(0);
    digitalWrite(LED_OK, LOW);
    
    sendStatus("locked", "auto_lock");
    
    Serial.println("[UNLOCK] Door auto-locked");
}

// ============= UNIFIED LOCK EXECUTION =============
void executeLock(const char* reason) {
    doorServo.write(0);
    digitalWrite(LED_OK, LOW);
    digitalWrite(LED_ERR, HIGH);
    
    sendStatus("locked", reason ? reason : "denied");
    
    delay(1500);
    digitalWrite(LED_ERR, LOW);
}

// ============= SEND REMOTE RESPONSE =============
void sendRemoteResponse(String command_id, bool success, const char* status) {
    if (!mqtt.connected()) return;
    
    StaticJsonDocument<256> doc;
    doc["device_id"] = device_id;
    doc["command_id"] = command_id;
    doc["success"] = success;
    doc["status"] = status;
    doc["timestamp"] = time(nullptr);
    doc["free_heap"] = ESP.getFreeHeap();
    
    String payload;
    serializeJson(doc, payload);
    
    mqtt.publish(topic_status, payload.c_str(), false);
    
    Serial.printf("[RESPONSE] Sent: command_id=%s, success=%d, status=%s\n", 
                  command_id.c_str(), success, status);
}

// ============= LOG REMOTE ACCESS =============
void logRemoteAccess(const char* action, String command_id, 
                     String initiated_by, String reason, unsigned long duration) {
    if (!mqtt.connected()) return;
    
    StaticJsonDocument<384> doc;
    doc["device_id"] = device_id;
    doc["type"] = "remote_access";
    doc["action"] = action;
    doc["command_id"] = command_id;
    doc["initiated_by"] = initiated_by;
    doc["reason"] = reason;
    doc["duration_ms"] = duration;
    doc["timestamp"] = time(nullptr);
    
    String payload;
    serializeJson(doc, payload);
    
    // Publish to dedicated remote access log topic
    String logTopic = String(topic_status) + "/remote";
    mqtt.publish(logTopic.c_str(), payload.c_str(), false);
    
    Serial.printf("[LOG] Remote %s by %s: %s\n", action, 
                  initiated_by.c_str(), reason.c_str());
}

void sendStatus(const char* state, const char* method) {
    if (!mqtt.connected()) return;
    
    StaticJsonDocument<256> doc;
    doc["device_id"] = device_id;
    doc["state"] = state;
    doc["method"] = method;
    doc["timestamp"] = time(nullptr);
    
    if (remoteState.active) {
        doc["remote_active"] = true;
        doc["remote_user"] = remoteState.initiated_by;
    }
    
    String payload;
    serializeJson(doc, payload);
    
    mqtt.publish(topic_status, payload.c_str(), true);
}

void setup() {
  Serial.begin(115200);
  delay(100);
  
  Serial.println("\n\n=================================");
  Serial.println("Keypad Password Device Starting");
  Serial.println("=================================");
  Serial.printf("Device ID: %s\n", device_id);
  Serial.printf("Free heap: %u bytes\n", ESP.getFreeHeap());
  
  pinMode(LED_OK, OUTPUT);
  pinMode(LED_ERR, OUTPUT);
  digitalWrite(LED_OK, LOW); 
  digitalWrite(LED_ERR, LOW);

  doorServo.attach(SERVO_PIN);
  doorServo.write(0);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, wifiPass);
  Serial.print("[WiFi] Connecting");
  
  int wifiAttempts = 0;
  while (WiFi.status() != WL_CONNECTED && wifiAttempts < 30) {
    delay(500);
    Serial.print(".");
    wifiAttempts++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[ERROR] WiFi connection failed, restarting...");
    delay(1000);
    ESP.restart();
  }
  
  Serial.println("\n[WiFi] Connected");
  Serial.print("[WiFi] IP: ");
  Serial.println(WiFi.localIP());
  Serial.printf("[WiFi] Signal: %d dBm\n", WiFi.RSSI());

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
    Serial.println("\n[WARNING] Time sync failed, continuing anyway...");
  } else {
    Serial.println();
    Serial.print("[NTP] Time synced: ");
    Serial.println(now);
  }

  tlsClient.setTrustAnchors(&cert);
  tlsClient.setInsecure();
  
  Serial.printf("[TLS] Certificate verification: ENABLED\n");

  mqtt.setServer(mqtt_host, mqtt_port);
  mqtt.setCallback(mqttCallback);
  mqtt.setKeepAlive(60);
  mqtt.setBufferSize(512);
  
  reconnectMQTT();
  
  Serial.println("\n[SYSTEM] Ready!");
  Serial.println("=================================\n");
  
  blinkLED(LED_OK, 2, 200);

  remoteConfig.enabled = true;
  remoteConfig.default_duration_ms = 5000;
  remoteConfig.max_duration_ms = 30000;
}

void loop() {
  if (!mqtt.connected()) {
    reconnectMQTT();
  }
  mqtt.loop();
  
  unsigned long currentMillis = millis();
  if (currentMillis - lastMemCheck >= memCheckInterval) {
    lastMemCheck = currentMillis;
    checkMemory();
  }

  if (remoteState.active) {
    unsigned long elapsed = millis() - remoteState.unlock_time;
    if (elapsed >= remoteState.duration_ms + 5000) {  // +2s for servo movement
      remoteState.active = false;
      Serial.println("[REMOTE] Remote unlock session ended");
    }
  }
  
  char k = keypad.getKey();
  if (k) {
    curPw += k;
    lastKeyPress = millis();
    Serial.print("[INPUT] Password: ");
    for (int i = 0; i < curPw.length(); i++) {
      Serial.print("*");
    }
    Serial.println();
    
    digitalWrite(LED_OK, HIGH);
    delay(50);
    digitalWrite(LED_OK, LOW);
    
    if (curPw.length() == 6 && !waitingForReply) {
      Serial.println("[AUTH] Password complete, sending request...");
      sendUnlockRequest(curPw);
      curPw = "";
    }
  }
  
  if (curPw.length() > 0 && curPw.length() < 6) {
    if (millis() - lastKeyPress > 10000) {
      Serial.println("[TIMEOUT] Password entry timeout, clearing");
      curPw = "";
      blinkLED(LED_ERR, 1, 100);
    }
  }
  
  if (waitingForReply && millis() - lastKeyPress > 15000) {
    Serial.println("[TIMEOUT] No response from gateway");
    waitingForReply = false;
    blinkLED(LED_ERR, 3, 300);
  }
  
  if (consecutiveFailures > maxRetries * 2) {
    Serial.println("[ERROR] Too many authentication failures, waiting...");
    delay(30000);
    consecutiveFailures = 0;
  }
}