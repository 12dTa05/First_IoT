/**
 * ESP8266 Keypad Door Controller - Security Improved
 * Features:
 * - HMAC-SHA256 authentication
 * - Replay attack protection (nonce + timestamp)
 * - TLS/SSL with certificate validation
 * - Rate limiting
 * - Watchdog timer
 */

#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Servo.h>
#include <Keypad.h>
#include <bearssl/bearssl_hmac.h>
#include <bearssl/bearssl_hash.h>
#include <time.h>
#include <Ticker.h>

// ========== Configuration ==========
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

const char* MQTT_HOST = "192.168.1.148";
const uint16_t MQTT_PORT = 1884;

const char* DEVICE_ID = "passkey_01";
const char* DEVICE_SALT = "passkey_01_salt_2025";

// Topics
const char* TOPIC_REQUEST = "home/devices/passkey_01/request";
const char* TOPIC_COMMAND = "home/devices/passkey_01/command";
const char* TOPIC_STATUS = "home/devices/passkey_01/status";

// Certificate - ADD YOUR CA CERT HERE
const char ROOT_CA_PEM[] = R"EOF(
-----BEGIN CERTIFICATE-----
[YOUR CA CERTIFICATE HERE]
-----END CERTIFICATE-----
)EOF";

// HMAC Key (must match gateway)
const uint8_t HMAC_KEY[32] = {
  0x5A, 0x5A, 0x2B, 0x3F, 0x87, 0xDA, 0x01, 0xF9,
  0xDE, 0xE1, 0x83, 0xAD, 0x84, 0x54, 0xB5, 0x34,
  0x77, 0x68, 0x47, 0x8C, 0xE8, 0xFD, 0x73, 0x1F,
  0xBD, 0xE1, 0x3C, 0x42, 0x79, 0xB8, 0xFE, 0xA4
};

// ========== Hardware Configuration ==========
const int LED_OK = D0;
const int LED_ERR = D1;
const int SERVO_PIN = D8;
const int BUZZER_PIN = D9;  // Optional buzzer

// Keypad Configuration (4x3)
const byte ROWS = 4;
const byte COLS = 3;
char keys[ROWS][COLS] = {
  {'1','2','3'},
  {'4','5','6'},
  {'7','8','9'},
  {'*','0','#'}
};
byte rowPins[ROWS] = { D2, D3, D4, D5 };
byte colPins[COLS] = { D6, D7, D10 };

Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// ========== Objects ==========
Servo doorServo;
BearSSL::X509List cert(ROOT_CA_PEM);
WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);
Ticker watchdogTimer;

// ========== State Variables ==========
String currentPassword = "";
bool waitingForReply = false;
unsigned long lastKeyPress = 0;
unsigned long lastRequestTime = 0;

// Rate limiting
const unsigned long RATE_LIMIT_WINDOW = 60000;  // 1 minute
const int MAX_REQUESTS_PER_MINUTE = 5;
int requestCount = 0;
unsigned long rateLimitWindowStart = 0;

// Watchdog
const unsigned long WATCHDOG_TIMEOUT = 60000;  // 60 seconds
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

String calculateSHA256(const String &data) {
  /**
   * Calculate SHA-256 hash with device salt
   */
  uint8_t hash[32];
  br_sha256_context ctx;
  br_sha256_init(&ctx);
  
  String salted = String(DEVICE_SALT) + data;
  br_sha256_update(&ctx, (const unsigned char*)salted.c_str(), salted.length());
  br_sha256_out(&ctx, hash);
  
  // Convert to hex (first 12 characters)
  char hex[65];
  for (int i = 0; i < 32; i++) {
    sprintf(hex + i * 2, "%02x", hash[i]);
  }
  return String(hex).substring(0, 12);
}

String calculateHMAC(const String &data) {
  /**
   * Calculate HMAC-SHA256 signature
   */
  uint8_t mac[32];
  br_hmac_key_context kc;
  br_hmac_context ctx;
  
  br_hmac_key_init(&kc, &br_sha256_vtable, HMAC_KEY, sizeof(HMAC_KEY));
  br_hmac_init(&ctx, &kc, 32);
  br_hmac_update(&ctx, (const unsigned char*)data.c_str(), data.length());
  br_hmac_out(&ctx, mac);
  
  // Convert to hex
  char hex[65];
  for (int i = 0; i < 32; i++) {
    sprintf(hex + i * 2, "%02x", mac[i]);
  }
  return String(hex);
}

bool checkRateLimit() {
  /**
   * Check if device is within rate limit
   */
  unsigned long now = millis();
  
  // Reset window if expired
  if (now - rateLimitWindowStart >= RATE_LIMIT_WINDOW) {
    rateLimitWindowStart = now;
    requestCount = 0;
  }
  
  if (requestCount >= MAX_REQUESTS_PER_MINUTE) {
    Serial.println("[RATE_LIMIT] Too many requests!");
    return false;
  }
  
  requestCount++;
  return true;
}

void publishStatus(const char* state, const char* reason = nullptr) {
  /**
   * Publish device status
   */
  StaticJsonDocument<128> doc;
  doc["device_id"] = DEVICE_ID;
  doc["state"] = state;
  doc["timestamp"] = time(nullptr);
  
  if (reason) {
    doc["reason"] = reason;
  }
  
  String payload;
  serializeJson(doc, payload);
  mqtt.publish(TOPIC_STATUS, payload.c_str());
  
  Serial.print("[STATUS] ");
  Serial.println(payload);
}

void sendUnlockRequest(const String &password) {
  /**
   * Send unlock request with HMAC signature
   */
  // Check rate limit
  if (!checkRateLimit()) {
    digitalWrite(LED_ERR, HIGH);
    delay(2000);
    digitalWrite(LED_ERR, LOW);
    return;
  }
  
  // Build body
  StaticJsonDocument<256> body;
  body["cmd"] = "unlock_request";
  body["client_id"] = DEVICE_ID;
  body["pw"] = calculateSHA256(password);
  body["ts"] = time(nullptr);
  body["nonce"] = random(0, 2147483647);
  
  String bodyStr;
  serializeJson(body, bodyStr);
  
  // Calculate HMAC
  String signature = calculateHMAC(bodyStr);
  
  // Wrap with HMAC
  StaticJsonDocument<384> wrapper;
  wrapper["body"] = bodyStr;
  wrapper["hmac"] = signature;
  
  String payload;
  serializeJson(wrapper, payload);
  
  // Publish
  if (mqtt.publish(TOPIC_REQUEST, payload.c_str())) {
    Serial.println("[REQUEST] Unlock request sent");
    waitingForReply = true;
    lastRequestTime = millis();
  } else {
    Serial.println("[ERROR] Failed to publish request");
  }
}

void handleCommand(const JsonDocument &doc) {
  /**
   * Handle command from gateway
   */
  const char* cmd = doc["cmd"];
  if (!cmd) return;
  
  if (strcmp(cmd, "OPEN") == 0) {
    // Open door
    Serial.println("[COMMAND] Opening door");
    
    doorServo.write(180);
    digitalWrite(LED_OK, HIGH);
    digitalWrite(LED_ERR, LOW);
    
    publishStatus("OPENED");
    
    // Auto-close after 5 seconds
    delay(5000);
    doorServo.write(0);
    digitalWrite(LED_OK, LOW);
    
    publishStatus("CLOSED");
    
  } else if (strcmp(cmd, "LOCK") == 0) {
    // Lock door
    Serial.println("[COMMAND] Locking door");
    
    doorServo.write(0);
    digitalWrite(LED_OK, LOW);
    digitalWrite(LED_ERR, HIGH);
    
    const char* reason = doc["reason"];
    publishStatus("LOCKED", reason);
    
    delay(2000);
    digitalWrite(LED_ERR, LOW);
  }
  
  waitingForReply = false;
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
  while (!mqtt.connected()) {
    Serial.print("[MQTT] Connecting...");
    
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println(" connected");
      
      // Subscribe to command topic
      mqtt.subscribe(TOPIC_COMMAND);
      Serial.print("[MQTT] Subscribed to: ");
      Serial.println(TOPIC_COMMAND);
      
      // Publish online status
      publishStatus("online");
      
    } else {
      Serial.print(" failed, rc=");
      Serial.println(mqtt.state());
      delay(5000);
    }
  }
}

// ========== Setup ==========

void setup() {
  Serial.begin(115200);
  Serial.println("\n\n=== Keypad Door Controller ===");
  Serial.print("Device ID: ");
  Serial.println(DEVICE_ID);
  
  // Initialize hardware
  pinMode(LED_OK, OUTPUT);
  pinMode(LED_ERR, OUTPUT);
  digitalWrite(LED_OK, LOW);
  digitalWrite(LED_ERR, LOW);
  
  doorServo.attach(SERVO_PIN);
  doorServo.write(0);  // Start locked
  
  // Connect WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] Connecting");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[ERROR] WiFi connection failed!");
    ESP.restart();
  }
  
  Serial.println("\n[WiFi] Connected");
  Serial.print("[WiFi] IP: ");
  Serial.println(WiFi.localIP());
  
  // Setup NTP for time sync
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
    Serial.println("\n[ERROR] Time sync failed!");
  } else {
    Serial.print("\n[NTP] Time synced: ");
    Serial.println(now);
  }
  
  // Setup TLS
  tlsClient.setTrustAnchors(&cert);
  // IMPORTANT: Remove setInsecure() for production!
  // Only use if you have certificate validation issues during development
  // tlsClient.setInsecure();
  
  // Setup MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);  // Increase buffer for larger messages
  
  // Initial connection
  reconnectMQTT();
  
  // Start watchdog timer
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
  
  // Check for keypad input
  char key = keypad.getKey();
  
  if (key) {
    Serial.print("[KEYPAD] Key pressed: ");
    Serial.println(key);
    
    if (key == '#') {
      // Submit password
      if (currentPassword.length() >= 4 && currentPassword.length() <= 8) {
        if (!waitingForReply) {
          Serial.print("[AUTH] Attempting unlock with password length: ");
          Serial.println(currentPassword.length());
          
          sendUnlockRequest(currentPassword);
        }
      } else {
        Serial.println("[ERROR] Invalid password length (4-8 digits)");
        digitalWrite(LED_ERR, HIGH);
        delay(500);
        digitalWrite(LED_ERR, LOW);
      }
      
      currentPassword = "";
      
    } else if (key == '*') {
      // Clear password
      currentPassword = "";
      Serial.println("[KEYPAD] Password cleared");
      
    } else {
      // Add digit to password
      if (currentPassword.length() < 8) {
        currentPassword += key;
        lastKeyPress = millis();
        
        // Visual feedback
        digitalWrite(LED_OK, HIGH);
        delay(100);
        digitalWrite(LED_OK, LOW);
      }
    }
  }
  
  // Timeout for password input (15 seconds)
  if (currentPassword.length() > 0 && 
      millis() - lastKeyPress > 15000) {
    Serial.println("[TIMEOUT] Password input timeout");
    currentPassword = "";
  }
  
  // Timeout for waiting reply (10 seconds)
  if (waitingForReply && 
      millis() - lastRequestTime > 10000) {
    Serial.println("[TIMEOUT] No response from gateway");
    waitingForReply = false;
    
    digitalWrite(LED_ERR, HIGH);
    delay(1500);
    digitalWrite(LED_ERR, LOW);
  }
  
  // Monitor heap memory
  static unsigned long lastHeapCheck = 0;
  if (millis() - lastHeapCheck > 30000) {
    lastHeapCheck = millis();
    Serial.print("[HEAP] Free: ");
    Serial.println(ESP.getFreeHeap());
    
    // Restart if heap is critically low
    if (ESP.getFreeHeap() < 8000) {
      Serial.println("[ERROR] Low memory, restarting...");
      delay(1000);
      ESP.restart();
    }
  }
  
  delay(10);  // Small delay to prevent tight loop
}