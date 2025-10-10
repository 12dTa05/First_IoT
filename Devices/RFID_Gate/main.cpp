/**
 * ESP8266 RFID Gate Controller with LoRa E32
 * 
 * Features:
 * - RFID card authentication via LoRa
 * - Compact binary protocol with CRC32
 * - Retry mechanism with exponential backoff
 * - Timeout handling and error recovery
 * - Gate status reporting
 * - Buzzer feedback (optional)
 * - Watchdog timer
 * 
 * Hardware:
 * - ESP8266 (NodeMCU v2 / Wemos D1 Mini)
 * - MFRC522 RFID Reader
 * - LoRa E32-TTL-100
 * - Servo Motor
 * - Buzzer (optional)
 */

#include <Arduino.h>
#include <SoftwareSerial.h>
#include <LoRa_E32.h>
#include <MFRC522.h>
#include <Servo.h>
#include <Ticker.h>

// ========== Configuration ==========
#define RESPONSE_TIMEOUT_MS 12000     // 12 seconds to wait for gateway response
#define MAX_RETRIES 3                 // Maximum retry attempts
#define RETRY_DELAY_MS 2000           // Initial retry delay
#define SCAN_DEBOUNCE_MS 3000         // Debounce time between card scans
#define GATE_OPEN_DURATION 5000       // How long gate stays open (ms)
#define WATCHDOG_TIMEOUT 30000        // Watchdog timeout (30 seconds)

// ========== Hardware Pin Definitions ==========
#define LORA_RX D2        // GPIO4 - LoRa TX connects here
#define LORA_TX D1        // GPIO5 - LoRa RX connects here
#define RFID_SS D8        // GPIO15 - RFID SDA
#define RFID_RST D3       // GPIO0 - RFID RST
#define SERVO_PIN D0      // GPIO16 - Servo control
#define BUZZER_PIN D4     // GPIO2 - Buzzer (optional)
#define STATUS_LED LED_BUILTIN  // Built-in LED for status

// ========== Hardware Objects ==========
SoftwareSerial loraSerial(LORA_RX, LORA_TX);
LoRa_E32 e32ttl(&loraSerial);
MFRC522 rfid(RFID_SS, RFID_RST);
Servo gateServo;
Ticker watchdogTimer;

// ========== Global Variables ==========
uint16_t sequenceNumber = 0;
unsigned long lastCardScan = 0;
volatile bool watchdogFed = false;

// Statistics
struct Statistics {
  uint32_t totalScans;
  uint32_t successfulAuth;
  uint32_t failedAuth;
  uint32_t loraErrors;
  uint32_t timeouts;
  unsigned long uptime;
} stats = {0, 0, 0, 0, 0, 0};

// ========== Protocol Message Types ==========
enum MessageType {
  MSG_RFID_SCAN = 0x01,
  MSG_TEMP_UPDATE = 0x02,
  MSG_MOTION_DETECT = 0x03,
  MSG_RELAY_CONTROL = 0x04,
  MSG_PASSKEY = 0x05,
  MSG_GATE_STATUS = 0x06,
  MSG_SYSTEM_STATUS = 0x07,
  MSG_DOOR_STATUS = 0x08,
  MSG_ACK = 0x80,
  MSG_ERROR = 0xFF
};

enum DeviceType {
  DEV_RFID_GATE = 0x01,
  DEV_RELAY_FAN = 0x02,
  DEV_TEMP_SENSOR = 0x03,
  DEV_GATEWAY = 0x04,
  DEV_PASSKEY = 0x05,
  DEV_MOTION_OUTDOOR = 0x07,
  DEV_MOTION_INDOOR = 0x08
};

// ========== Watchdog Functions ==========

void feedWatchdog() {
  watchdogFed = true;
  digitalWrite(STATUS_LED, !digitalRead(STATUS_LED));  // Toggle LED
}

void watchdogCheck() {
  if (!watchdogFed) {
    Serial.println(F("\n[WATCHDOG] System hung, rebooting..."));
    delay(100);
    ESP.restart();
  }
  watchdogFed = false;
}

// ========== Utility Functions ==========

unsigned long getCurrentTimestamp() {
  return millis() / 1000;
}

uint32_t calculateCRC32(const uint8_t* data, size_t len) {
  /**
   * CRC32 calculation for data integrity verification
   * Polynomial: 0x04C11DB7 (standard CRC-32)
   */
  const uint32_t polynomial = 0x04C11DB7;
  uint32_t crc = 0xFFFFFFFF;
  
  for (size_t i = 0; i < len; i++) {
    crc ^= ((uint32_t)data[i] << 24);
    
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x80000000) {
        crc = (crc << 1) ^ polynomial;
      } else {
        crc <<= 1;
      }
    }
  }
  
  return crc ^ 0xFFFFFFFF;
}

void printHex(const uint8_t* data, size_t len) {
  /**
   * Print byte array in hex format for debugging
   */
  for (size_t i = 0; i < len; i++) {
    if (data[i] < 0x10) Serial.print('0');
    Serial.print(data[i], HEX);
    Serial.print(' ');
  }
  Serial.println();
}

// ========== Buzzer Functions ==========

void playTone(int frequency, int duration) {
  #ifdef BUZZER_PIN
  tone(BUZZER_PIN, frequency, duration);
  #endif
}

void playSuccessSound() {
  playTone(1000, 100);
  delay(100);
  playTone(1200, 100);
}

void playErrorSound() {
  playTone(400, 200);
  delay(100);
  playTone(400, 200);
}

void playStartupSound() {
  playTone(800, 100);
  delay(50);
  playTone(1000, 100);
  delay(50);
  playTone(1200, 100);
}

// ========== LoRa Message Functions ==========

bool sendRFIDScan(const byte* uid, byte uidLen) {
  /**
   * Send RFID scan message via LoRa with compact binary protocol
   * 
   * Message format:
   * [Header: 3 bytes] [Version+MsgType: 1] [Flags+DevType: 1] 
   * [Seq: 2] [Timestamp: 4] [PayloadLen: 1] [Payload: N] [CRC32: 4]
   */
  
  if (uidLen > 10) {
    Serial.println(F("[ERROR] UID too long"));
    return false;
  }
  
  uint8_t buffer[64];
  int idx = 0;
  
  // Protocol header prefix (0x00 0x02 0x17)
  buffer[idx++] = 0x00;
  buffer[idx++] = 0x02;
  buffer[idx++] = 0x17;
  
  // Header byte 0: [MsgType: 4 bits][Version: 4 bits]
  // MsgType = 1 (RFID_SCAN), Version = 1
  buffer[idx++] = (MSG_RFID_SCAN << 4) | 0x01;
  
  // Header byte 1: [Flags: 4 bits][DeviceType: 4 bits]
  // Flags = 0, DeviceType = 1 (RFID_GATE)
  buffer[idx++] = (0x00 << 4) | DEV_RFID_GATE;
  
  // Sequence number (little-endian, 16-bit)
  buffer[idx++] = (sequenceNumber & 0xFF);
  buffer[idx++] = (sequenceNumber >> 8);
  
  // Timestamp (little-endian, 32-bit)
  uint32_t ts = getCurrentTimestamp();
  buffer[idx++] = (ts & 0xFF);
  buffer[idx++] = (ts >> 8) & 0xFF;
  buffer[idx++] = (ts >> 16) & 0xFF;
  buffer[idx++] = (ts >> 24) & 0xFF;
  
  // Payload length
  buffer[idx++] = uidLen;
  
  // Payload: UID bytes
  for (byte i = 0; i < uidLen; i++) {
    buffer[idx++] = uid[i];
  }
  
  // CRC32 (calculated from header to end of payload, excluding prefix)
  uint32_t crc = calculateCRC32(&buffer[3], idx - 3);
  buffer[idx++] = (crc & 0xFF);
  buffer[idx++] = (crc >> 8) & 0xFF;
  buffer[idx++] = (crc >> 16) & 0xFF;
  buffer[idx++] = (crc >> 24) & 0xFF;
  
  // Send via LoRa
  ResponseStatus rs = e32ttl.sendMessage(buffer, idx);
  
  if (rs.code == 1) {
    Serial.print(F("[LORA] Sent RFID scan: seq="));
    Serial.print(sequenceNumber);
    Serial.print(F(", len="));
    Serial.print(idx);
    Serial.print(F(", UID="));
    printHex(uid, uidLen);
    
    sequenceNumber++;
    return true;
  } else {
    Serial.print(F("[ERROR] LoRa send failed: "));
    Serial.println(rs.getResponseDescription());
    stats.loraErrors++;
    return false;
  }
}

bool sendStatusMessage(const char* status) {
  /**
   * Send gate status update to gateway
   * Status can be: "open", "clos" (closed), "erro" (error)
   */
  
  uint8_t buffer[64];
  int idx = 0;
  
  // Protocol header
  buffer[idx++] = 0x00;
  buffer[idx++] = 0x02;
  buffer[idx++] = 0x17;
  
  // Header: MsgType = 6 (GATE_STATUS), Version = 1
  buffer[idx++] = (MSG_GATE_STATUS << 4) | 0x01;
  
  // Header: Flags = 0, DeviceType = 1 (RFID_GATE)
  buffer[idx++] = (0x00 << 4) | DEV_RFID_GATE;
  
  // Sequence number
  buffer[idx++] = (sequenceNumber & 0xFF);
  buffer[idx++] = (sequenceNumber >> 8);
  
  // Timestamp
  uint32_t ts = getCurrentTimestamp();
  buffer[idx++] = (ts & 0xFF);
  buffer[idx++] = (ts >> 8) & 0xFF;
  buffer[idx++] = (ts >> 16) & 0xFF;
  buffer[idx++] = (ts >> 24) & 0xFF;
  
  // Payload: status string
  uint8_t statusLen = strlen(status);
  buffer[idx++] = statusLen;
  for (uint8_t i = 0; i < statusLen; i++) {
    buffer[idx++] = status[i];
  }
  
  // CRC32
  uint32_t crc = calculateCRC32(&buffer[3], idx - 3);
  buffer[idx++] = (crc & 0xFF);
  buffer[idx++] = (crc >> 8) & 0xFF;
  buffer[idx++] = (crc >> 16) & 0xFF;
  buffer[idx++] = (crc >> 24) & 0xFF;
  
  // Send
  ResponseStatus rs = e32ttl.sendMessage(buffer, idx);
  
  if (rs.code == 1) {
    Serial.print(F("[LORA] Sent status: "));
    Serial.println(status);
    sequenceNumber++;
    return true;
  } else {
    stats.loraErrors++;
    return false;
  }
}

bool receiveResponse(bool* accessGranted, unsigned long timeoutMs) {
  /**
   * Wait for and validate response from gateway
   * 
   * Expected format: 
   * C0 00 00 [Address:2 bytes] [Channel:1] [Length:1] [Status:5 bytes]
   * 
   * Status values:
   * - "GRANT" = Access granted
   * - "DENY5" = Access denied
   */
  
  unsigned long startTime = millis();
  
  Serial.print(F("[LORA] Waiting for response (timeout="));
  Serial.print(timeoutMs);
  Serial.println(F("ms)..."));
  
  while (millis() - startTime < timeoutMs) {
    feedWatchdog();  // Keep feeding watchdog while waiting
    
    if (e32ttl.available() > 0) {
      ResponseContainer rc = e32ttl.receiveMessage();
      
      if (rc.status.code == 1 && rc.data.length() >= 12) {
        const uint8_t* buffer = (const uint8_t*)rc.data.c_str();
        int len = rc.data.length();
        
        Serial.print(F("[LORA] Received: len="));
        Serial.print(len);
        Serial.print(F(", data="));
        printHex(buffer, len);
        
        // Verify header (C0 00 00)
        if (buffer[0] != 0xC0 || buffer[1] != 0x00 || buffer[2] != 0x00) {
          Serial.println(F("[ERROR] Invalid response header"));
          continue;
        }
        
        // Verify channel (0x17 = 23)
        if (buffer[5] != 0x17) {
          Serial.println(F("[ERROR] Invalid channel"));
          continue;
        }
        
        // Extract status length
        uint8_t statusLen = buffer[6];
        
        // Verify total length (header:3 + addr:2 + chan:1 + len:1 + status:N)
        if (len != 7 + statusLen) {
          Serial.print(F("[ERROR] Invalid length: expected="));
          Serial.print(7 + statusLen);
          Serial.print(F(", got="));
          Serial.println(len);
          continue;
        }
        
        // Extract status string
        String status = "";
        for (uint8_t i = 0; i < statusLen; i++) {
          status += (char)buffer[7 + i];
        }
        
        Serial.print(F("[LORA] Response status: "));
        Serial.println(status);
        
        // Check status
        if (status == "GRANT") {
          *accessGranted = true;
          return true;
        } else if (status == "DENY5") {
          *accessGranted = false;
          return true;
        } else {
          Serial.println(F("[ERROR] Unknown status"));
          continue;
        }
      }
    }
    
    delay(10);  // Small delay to prevent tight loop
  }
  
  Serial.println(F("[TIMEOUT] No valid response received"));
  stats.timeouts++;
  return false;
}

// ========== Gate Control Functions ==========

void openGate() {
  /**
   * Open gate sequence with status reporting
   */
  Serial.println(F("\n[GATE] ===== OPENING GATE ====="));
  
  // Move servo to open position
  gateServo.write(180);
  
  // Send status update
  sendStatusMessage("open");
  
  // Success sound
  playSuccessSound();
  
  // Visual feedback
  for (int i = 0; i < 3; i++) {
    digitalWrite(STATUS_LED, LOW);
    delay(100);
    digitalWrite(STATUS_LED, HIGH);
    delay(100);
  }
  
  // Keep gate open
  Serial.print(F("[GATE] Open for "));
  Serial.print(GATE_OPEN_DURATION / 1000);
  Serial.println(F(" seconds..."));
  
  unsigned long startTime = millis();
  while (millis() - startTime < GATE_OPEN_DURATION) {
    feedWatchdog();
    delay(100);
  }
  
  // Close gate
  Serial.println(F("[GATE] Closing..."));
  gateServo.write(0);
  sendStatusMessage("clos");
  
  Serial.println(F("[GATE] ===== GATE CLOSED =====\n"));
  
  stats.successfulAuth++;
}

void denyAccess() {
  /**
   * Deny access feedback
   */
  Serial.println(F("[GATE] ===== ACCESS DENIED =====\n"));
  
  playErrorSound();
  
  // Visual feedback
  for (int i = 0; i < 5; i++) {
    digitalWrite(STATUS_LED, LOW);
    delay(100);
    digitalWrite(STATUS_LED, HIGH);
    delay(100);
  }
  
  stats.failedAuth++;
}

// ========== Setup Function ==========

void setup() {
  // Initialize serial
  Serial.begin(9600);
  delay(100);
  
  Serial.println(F("\n\n"));
  Serial.println(F("===================================="));
  Serial.println(F("   RFID Gate Controller v2.0"));
  Serial.println(F("===================================="));
  Serial.println(F("Device: RFID Gate with LoRa E32"));
  Serial.println(F("Protocol: Compact Binary + CRC32"));
  Serial.println(F("====================================\n"));
  
  // Initialize status LED
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, HIGH);
  
  // Initialize buzzer
  #ifdef BUZZER_PIN
  pinMode(BUZZER_PIN, OUTPUT);
  #endif
  
  // Initialize LoRa
  Serial.print(F("[INIT] LoRa E32... "));
  loraSerial.begin(9600);
  e32ttl.begin();
  Serial.println(F("OK"));
  
  // Initialize RFID
  Serial.print(F("[INIT] MFRC522 RFID... "));
  SPI.begin();
  rfid.PCD_Init();
  
  // Check RFID reader
  byte version = rfid.PCD_ReadRegister(rfid.VersionReg);
  if (version == 0x00 || version == 0xFF) {
    Serial.println(F("FAILED!"));
    Serial.println(F("[ERROR] RFID reader not found!"));
    playErrorSound();
  } else {
    Serial.print(F("OK (version=0x"));
    Serial.print(version, HEX);
    Serial.println(F(")"));
  }
  
  // Initialize servo
  Serial.print(F("[INIT] Servo... "));
  gateServo.attach(SERVO_PIN);
  gateServo.write(0);  // Start in closed position
  Serial.println(F("OK (Position: CLOSED)"));
  
  // Start watchdog timer
  Serial.print(F("[INIT] Watchdog timer... "));
  watchdogTimer.attach(WATCHDOG_TIMEOUT / 1000, watchdogCheck);
  Serial.println(F("OK"));
  
  // Startup sound
  playStartupSound();
  
  // Random seed for sequence numbers
  randomSeed(analogRead(A0));
  sequenceNumber = random(0, 65535);
  
  // Startup complete
  Serial.println(F("\n[READY] System initialized and ready!"));
  Serial.print(F("[INFO] Initial sequence number: "));
  Serial.println(sequenceNumber);
  Serial.println(F("====================================\n"));
  
  stats.uptime = millis();
  
  // Blink LED to indicate ready
  for (int i = 0; i < 3; i++) {
    digitalWrite(STATUS_LED, LOW);
    delay(200);
    digitalWrite(STATUS_LED, HIGH);
    delay(200);
  }
}

// ========== Main Loop ==========

void loop() {
  feedWatchdog();
  
  // Check for RFID card
  if (!rfid.PICC_IsNewCardPresent()) {
    delay(50);
    return;
  }
  
  if (!rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }
  
  // Debounce check
  if (millis() - lastCardScan < SCAN_DEBOUNCE_MS) {
    Serial.println(F("[DEBOUNCE] Card scan too soon, ignoring"));
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    return;
  }
  
  lastCardScan = millis();
  stats.totalScans++;
  
  Serial.println(F("\n===================================="));
  Serial.println(F("[RFID] Card detected!"));
  Serial.println(F("===================================="));
  
  // Validate UID
  if (rfid.uid.size == 0 || rfid.uid.size > 10) {
    Serial.println(F("[ERROR] Invalid UID size"));
    denyAccess();
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(2000);
    return;
  }
  
  // Print UID
  Serial.print(F("[RFID] UID: "));
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) Serial.print('0');
    Serial.print(rfid.uid.uidByte[i], HEX);
    if (i < rfid.uid.size - 1) Serial.print(':');
  }
  Serial.print(F(" ("));
  Serial.print(rfid.uid.size);
  Serial.println(F(" bytes)"));
  
  // Send RFID scan with retry mechanism
  bool sent = false;
  for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
    Serial.print(F("\n[ATTEMPT] Sending scan ("));
    Serial.print(attempt + 1);
    Serial.print(F("/"));
    Serial.print(MAX_RETRIES);
    Serial.println(F(")..."));
    
    if (sendRFIDScan(rfid.uid.uidByte, rfid.uid.size)) {
      sent = true;
      break;
    }
    
    if (attempt < MAX_RETRIES - 1) {
      // Exponential backoff
      unsigned long retryDelay = RETRY_DELAY_MS * (1 << attempt);
      Serial.print(F("[RETRY] Waiting "));
      Serial.print(retryDelay);
      Serial.println(F("ms before retry..."));
      delay(retryDelay);
    }
  }
  
  if (!sent) {
    Serial.println(F("\n[ERROR] Failed to send after all retries"));
    sendStatusMessage("erro");
    denyAccess();
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(2000);
    return;
  }
  
  // Wait for gateway response
  bool accessGranted = false;
  if (receiveResponse(&accessGranted, RESPONSE_TIMEOUT_MS)) {
    if (accessGranted) {
      Serial.println(F("\n[AUTH] ✓ Access GRANTED"));
      openGate();
    } else {
      Serial.println(F("\n[AUTH] ✗ Access DENIED"));
      denyAccess();
    }
  } else {
    Serial.println(F("\n[ERROR] Communication timeout"));
    sendStatusMessage("erro");
    denyAccess();
  }
  
  // Print statistics
  Serial.println(F("\n-------- Statistics --------"));
  Serial.print(F("Total scans: "));
  Serial.println(stats.totalScans);
  Serial.print(F("Successful: "));
  Serial.println(stats.successfulAuth);
  Serial.print(F("Failed: "));
  Serial.println(stats.failedAuth);
  Serial.print(F("LoRa errors: "));
  Serial.println(stats.loraErrors);
  Serial.print(F("Timeouts: "));
  Serial.println(stats.timeouts);
  Serial.print(F("Uptime: "));
  Serial.print((millis() - stats.uptime) / 1000);
  Serial.println(F(" seconds"));
  Serial.println(F("----------------------------\n"));
  
  // Clean up RFID
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  
  // Wait before next scan
  delay(2000);
}