/**
 * ESP8266 Temperature & Humidity Sensor (DHT11)
 * Features:
 * - Non-blocking operation
 * - Automatic reconnection
 * - LCD display
 * - Error handling and recovery
 * - Memory monitoring
 */

#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <LiquidCrystal_I2C.h>
#include <time.h>
#include <Ticker.h>

// ========== Configuration ==========
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

const char* MQTT_HOST = "192.168.1.148";
const uint16_t MQTT_PORT = 1883;  // Non-TLS (publish-only device)

const char* DEVICE_ID = "temp_01";

// Topics
const char* TOPIC_TELEMETRY = "home/devices/temp_01/telemetry";
const char* TOPIC_STATUS = "home/devices/temp_01/status";

// ========== Hardware Configuration ==========
#define DHTPIN D4
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);
LiquidCrystal_I2C lcd(0x27, 16, 2);  // Try 0x3F if 0x27 doesn't work

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
Ticker watchdogTimer;

// ========== State Variables ==========
float lastTemperature = 0.0;
float lastHumidity = 0.0;
bool sensorError = false;
int consecutiveErrors = 0;

// Timing
unsigned long lastTelemetry = 0;
unsigned long lastDisplay = 0;
unsigned long lastReconnect = 0;

const unsigned long TELEMETRY_INTERVAL = 30000;  // 30 seconds
const unsigned long DISPLAY_INTERVAL = 2000;     // 2 seconds
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

void updateLCD(float temp, float humidity, bool error) {
  /**
   * Update LCD display
   */
  lcd.clear();
  
  if (error) {
    lcd.setCursor(0, 0);
    lcd.print("Sensor Error!");
    lcd.setCursor(0, 1);
    lcd.print("Check DHT11");
  } else {
    // Line 1: Temperature
    lcd.setCursor(0, 0);
    lcd.print("Temp: ");
    lcd.print(temp, 1);
    lcd.print((char)223);  // Degree symbol
    lcd.print("C");
    
    // Line 2: Humidity
    lcd.setCursor(0, 1);
    lcd.print("Humi: ");
    lcd.print(humidity, 1);
    lcd.print("%");
  }
}

void publishTelemetry(float temp, float humidity) {
  /**
   * Publish telemetry data
   */
  StaticJsonDocument<256> doc;
  doc["device_id"] = DEVICE_ID;
  doc["msg_type"] = "temp_update";
  doc["timestamp"] = time(nullptr);
  
  JsonObject data = doc.createNestedObject("data");
  data["temperature"] = temp;
  data["humidity"] = humidity;
  data["unit_temp"] = "C";
  data["unit_humidity"] = "%";
  
  String payload;
  serializeJson(doc, payload);
  
  if (mqtt.publish(TOPIC_TELEMETRY, payload.c_str())) {
    Serial.print("[TELEMETRY] Sent: T=");
    Serial.print(temp);
    Serial.print("C, H=");
    Serial.print(humidity);
    Serial.println("%");
  } else {
    Serial.println("[ERROR] Telemetry publish failed");
  }
}

void publishStatus(const char* state, const char* error = nullptr) {
  /**
   * Publish device status
   */
  StaticJsonDocument<128> doc;
  doc["device_id"] = DEVICE_ID;
  doc["state"] = state;
  doc["timestamp"] = time(nullptr);
  
  if (error) {
    doc["error"] = error;
  }
  
  String payload;
  serializeJson(doc, payload);
  mqtt.publish(TOPIC_STATUS, payload.c_str());
  
  Serial.print("[STATUS] ");
  Serial.println(payload);
}

bool readSensor() {
  /**
   * Read DHT11 sensor with error handling
   */
  float temp = dht.readTemperature();
  float humidity = dht.readHumidity();
  
  if (isnan(temp) || isnan(humidity)) {
    consecutiveErrors++;
    
    if (consecutiveErrors >= 3) {
      sensorError = true;
      Serial.println("[ERROR] DHT11 read error (3+ consecutive)");
      return false;
    }
    
    // Try using last valid values
    return false;
  }
  
  // Sanity check
  if (temp < -20 || temp > 60 || humidity < 0 || humidity > 100) {
    Serial.println("[ERROR] Sensor values out of range");
    consecutiveErrors++;
    return false;
  }
  
  // Valid reading
  consecutiveErrors = 0;
  sensorError = false;
  lastTemperature = temp;
  lastHumidity = humidity;
  
  return true;
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
      publishStatus("online");
      
    } else {
      Serial.print(" failed, rc=");
      Serial.println(mqtt.state());
    }
  }
}

// ========== Setup ==========

void setup() {
  Serial.begin(115200);
  Serial.println("\n\n=== Temperature Monitor ===");
  Serial.print("Device ID: ");
  Serial.println(DEVICE_ID);
  
  // Initialize DHT11
  dht.begin();
  delay(2000);  // DHT11 needs time to stabilize
  
  // Initialize LCD
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Starting...");
  
  // Connect WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] Connecting");
  
  lcd.setCursor(0, 1);
  lcd.print("WiFi...");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n[ERROR] WiFi connection failed!");
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("WiFi Failed!");
    delay(5000);
    ESP.restart();
  }
  
  Serial.println("\n[WiFi] Connected");
  Serial.print("[WiFi] IP: ");
  Serial.println(WiFi.localIP());
  
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("WiFi OK");
  lcd.setCursor(0, 1);
  lcd.print(WiFi.localIP());
  delay(2000);
  
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
  
  // Setup MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  
  // Initial connection
  reconnectMQTT();
  
  // Start watchdog
  watchdogTimer.attach(WATCHDOG_TIMEOUT / 1000, watchdogCheck);
  
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Ready!");
  delay(1000);
  
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
  
  // Read sensor and update display
  if (currentMillis - lastDisplay >= DISPLAY_INTERVAL) {
    lastDisplay = currentMillis;
    
    if (readSensor()) {
      updateLCD(lastTemperature, lastHumidity, false);
      
      Serial.print("[SENSOR] T=");
      Serial.print(lastTemperature);
      Serial.print("C, H=");
      Serial.print(lastHumidity);
      Serial.println("%");
      
    } else {
      updateLCD(0, 0, true);
    }
  }
  
  // Send telemetry
  if (currentMillis - lastTelemetry >= TELEMETRY_INTERVAL) {
    lastTelemetry = currentMillis;
    
    if (!sensorError && mqtt.connected()) {
      publishTelemetry(lastTemperature, lastHumidity);
    } else if (sensorError) {
      publishStatus("error", "sensor_read_failed");
    }
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