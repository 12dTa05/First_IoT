#include <Arduino.h>
#include <SoftwareSerial.h>
#include <LoRa_E32.h>
#include <MFRC522.h>
#include <Servo.h>

// ============= CONFIGURATION =============
#define DEVICE_ID "rfid_gate_01"
#define LORA_RX D2
#define LORA_TX D1
#define SS_PIN D8
#define RST_PIN D3
#define SERVO_PIN D0
#define LED_OK D4
#define LED_ERROR D5
#define RESPONSE_TIMEOUT_MS 12000

// Device type cho RFID Gate
#define DEVICE_TYPE_RFID_GATE 0x01

// Message types
#define MSG_TYPE_RFID_SCAN 0x01
#define MSG_TYPE_GATE_STATUS 0x06

// ============= HARDWARE =============
SoftwareSerial loraSerial(LORA_RX, LORA_TX);
LoRa_E32 lora(&loraSerial);
MFRC522 rfid(SS_PIN, RST_PIN);
Servo gate;

// ============= STATE =============
uint16_t seq = 0;

// ============= CRC32 (giống Gateway Python) =============
uint32_t crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFF;
  const uint32_t poly = 0x04C11DB7;
  
  for (size_t i = 0; i < len; i++) {
    crc ^= ((uint32_t)data[i] << 24);
    for (uint8_t bit = 0; bit < 8; bit++) {
      if (crc & 0x80000000) {
        crc = (crc << 1) ^ poly;
      } else {
        crc = crc << 1;
      }
    }
  }
  return crc ^ 0xFFFFFFFF;
}

// ============= MESSAGE BUILDING =============
bool sendRFIDScan(const byte* uid, byte uidLen) {
  if (uidLen > 10) return false;
  
  uint8_t buffer[64];
  int idx = 0;
  
  // Prefix (3 bytes): 0x00 0x02 0x17
  buffer[idx++] = 0x00;
  buffer[idx++] = 0x02;
  buffer[idx++] = 0x17;
  
  // Header byte 0: [msg_type(4 bits)][version(4 bits)]
  // msg_type = 0x01 (RFID), version = 0x01
  uint8_t header0 = (MSG_TYPE_RFID_SCAN << 4) | 0x01;
  buffer[idx++] = header0;
  
  // Header byte 1: [flags(4 bits)][device_type(4 bits)]
  // flags = 0x00, device_type = 0x01 (RFID gate)
  uint8_t header1 = (0x00 << 4) | DEVICE_TYPE_RFID_GATE;
  buffer[idx++] = header1;
  
  // Sequence number (2 bytes, little-endian)
  buffer[idx++] = (seq & 0xFF);
  buffer[idx++] = (seq >> 8);
  seq++;
  
  // Timestamp (4 bytes, little-endian) - seconds since boot
  uint32_t timestamp = millis() / 1000;
  buffer[idx++] = (timestamp & 0xFF);
  buffer[idx++] = (timestamp >> 8) & 0xFF;
  buffer[idx++] = (timestamp >> 16) & 0xFF;
  buffer[idx++] = (timestamp >> 24) & 0xFF;
  
  // Payload length (1 byte)
  buffer[idx++] = uidLen;
  
  // Payload: UID bytes
  for (byte i = 0; i < uidLen; i++) {
    buffer[idx++] = uid[i];
  }
  
  // Calculate CRC32 over header + payload (from buffer[3] to current idx)
  uint32_t crc = crc32(&buffer[3], idx - 3);
  
  // Append CRC32 (4 bytes, little-endian)
  buffer[idx++] = (crc & 0xFF);
  buffer[idx++] = (crc >> 8) & 0xFF;
  buffer[idx++] = (crc >> 16) & 0xFF;
  buffer[idx++] = (crc >> 24) & 0xFF;
  
  // Send via LoRa
  lora.sendMessage(buffer, idx);
  
  Serial.print(F("RFID TX: "));
  for (byte i = 0; i < uidLen; i++) {
    if (uid[i] < 0x10) Serial.print("0");
    Serial.print(uid[i], HEX);
  }
  Serial.print(F(" ("));
  Serial.print(idx);
  Serial.println(F(" bytes)"));
  
  return true;
}

bool sendStatusMessage(const char* status) {
  uint8_t statusLen = strlen(status);
  if (statusLen > 16) statusLen = 16;
  
  uint8_t buffer[64];
  int idx = 0;
  
  // Prefix
  buffer[idx++] = 0x00;
  buffer[idx++] = 0x02;
  buffer[idx++] = 0x17;
  
  // Header byte 0: msg_type = 0x06 (gate status), version = 0x01
  uint8_t header0 = (MSG_TYPE_GATE_STATUS << 4) | 0x01;
  buffer[idx++] = header0;
  
  // Header byte 1: flags = 0x00, device_type = 0x01
  uint8_t header1 = (0x00 << 4) | DEVICE_TYPE_RFID_GATE;
  buffer[idx++] = header1;
  
  // Sequence
  buffer[idx++] = (seq & 0xFF);
  buffer[idx++] = (seq >> 8);
  seq++;
  
  // Timestamp
  uint32_t timestamp = millis() / 1000;
  buffer[idx++] = (timestamp & 0xFF);
  buffer[idx++] = (timestamp >> 8) & 0xFF;
  buffer[idx++] = (timestamp >> 16) & 0xFF;
  buffer[idx++] = (timestamp >> 24) & 0xFF;
  
  // Payload length
  buffer[idx++] = statusLen;
  
  // Payload: status string
  for (uint8_t i = 0; i < statusLen; i++) {
    buffer[idx++] = status[i];
  }
  
  // CRC32
  uint32_t crc = crc32(&buffer[3], idx - 3);
  buffer[idx++] = (crc & 0xFF);
  buffer[idx++] = (crc >> 8) & 0xFF;
  buffer[idx++] = (crc >> 16) & 0xFF;
  buffer[idx++] = (crc >> 24) & 0xFF;
  
  lora.sendMessage(buffer, idx);
  
  Serial.print(F("Status TX: "));
  Serial.print(status);
  Serial.print(F(" ("));
  Serial.print(idx);
  Serial.println(F(" bytes)"));
  
  return true;
}

// ============= RECEIVE ACK FROM GATEWAY =============
bool receiveAckMessage(bool* accessGranted, unsigned long timeoutMs) {
  unsigned long startTime = millis();
  
  while (millis() - startTime < timeoutMs) {
    if (lora.available() > 0) {
      ResponseContainer rsc = lora.receiveMessage();
      
      if (rsc.status.code != 1 || rsc.data.length() < 12) {
        Serial.println(F("RX: invalid packet"));
        continue;
      }
      
      const uint8_t* buffer = (const uint8_t*)rsc.data.c_str();
      int len = rsc.data.length();
      
      // Verify header: 0xC0 0x00 0x00
      if (buffer[0] != 0xC0 || buffer[1] != 0x00 || buffer[2] != 0x00) {
        Serial.println(F("RX: invalid header"));
        continue;
      }
      
      // Verify channel at offset 5: should be 0x17 (23)
      if (buffer[5] != 0x17) {
        Serial.println(F("RX: invalid channel"));
        continue;
      }
      
      // Get status length at offset 6
      uint8_t statusLen = buffer[6];
      
      // Validate buffer size
      if (len != 7 + statusLen) {
        Serial.println(F("RX: size mismatch"));
        continue;
      }
      
      // Extract status string starting at offset 7
      String status = "";
      for (uint8_t i = 0; i < statusLen; i++) {
        status += (char)buffer[7 + i];
      }
      
      Serial.print(F("RX: "));
      Serial.println(status);
      
      // Check status
      if (status == "GRANT") {
        *accessGranted = true;
        return true;
      } else if (status == "DENY5") {
        *accessGranted = false;
        return true;
      } else {
        Serial.println(F("RX: unknown status"));
        continue;
      }
    }
    delay(10);
  }
  
  Serial.println(F("RX: timeout"));
  return false;
}

// ============= HARDWARE CONTROL =============
void openGate() {
  Serial.println(F("=== ACCESS GRANTED ==="));
  digitalWrite(LED_OK, HIGH);
  digitalWrite(LED_ERROR, LOW);
  
  gate.write(90);
  sendStatusMessage("open");
  
  delay(5000);
  
  gate.write(0);
  sendStatusMessage("clos");
  
  digitalWrite(LED_OK, LOW);
  Serial.println(F("Gate closed"));
}

void showError() {
  Serial.println(F("=== ACCESS DENIED ==="));
  digitalWrite(LED_ERROR, HIGH);
  digitalWrite(LED_OK, LOW);
  
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_ERROR, LOW);
    delay(200);
    digitalWrite(LED_ERROR, HIGH);
    delay(200);
  }
  
  delay(1000);
  digitalWrite(LED_ERROR, LOW);
}

// ============= SETUP =============
void setup() {
  Serial.begin(9600);
  delay(100);
  
  Serial.println(F("\n================================"));
  Serial.println(F("RFID Gate with LoRa"));
  Serial.println(F("Device: " DEVICE_ID));
  Serial.println(F("Protocol: Gateway Compatible"));
  Serial.println(F("================================\n"));
  
  // Setup LEDs
  pinMode(LED_OK, OUTPUT);
  pinMode(LED_ERROR, OUTPUT);
  digitalWrite(LED_OK, LOW);
  digitalWrite(LED_ERROR, LOW);
  
  // Setup LoRa
  loraSerial.begin(9600);
  lora.begin();
  Serial.println(F("[OK] LoRa initialized"));
  
  // Setup RFID
  SPI.begin();
  rfid.PCD_Init();
  Serial.println(F("[OK] RFID initialized"));
  
  // Setup Servo
  gate.attach(SERVO_PIN);
  gate.write(0);
  Serial.println(F("[OK] Servo initialized"));
  
  // Initialize random seed
  randomSeed(analogRead(A0));
  
  // Send online status
  sendStatusMessage("ONLINE");
  
  Serial.println(F("\n[READY] Waiting for RFID cards...\n"));
  
  // Blink LEDs to indicate ready
  for (int i = 0; i < 2; i++) {
    digitalWrite(LED_OK, HIGH);
    delay(100);
    digitalWrite(LED_OK, LOW);
    delay(100);
  }
}

// ============= MAIN LOOP =============
void loop() {
  // Check for RFID card
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }
  
  Serial.println(F("\n--- RFID Card Detected ---"));
  
  // Validate UID
  if (rfid.uid.size == 0 || rfid.uid.size > 10) {
    Serial.println(F("[ERROR] Invalid UID size"));
    showError();
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(2000);
    return;
  }
  
  // Copy UID
  byte uid[10];
  byte uidLen = rfid.uid.size;
  memcpy(uid, rfid.uid.uidByte, uidLen);
  
  // Send RFID scan message
  if (!sendRFIDScan(uid, uidLen)) {
    Serial.println(F("[ERROR] Failed to send message"));
    showError();
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(2000);
    return;
  }
  
  // Wait for ACK from Gateway
  bool accessGranted = false;
  if (receiveAckMessage(&accessGranted, RESPONSE_TIMEOUT_MS)) {
    if (accessGranted) {
      openGate();
    } else {
      showError();
    }
  } else {
    Serial.println(F("[ERROR] No response from Gateway"));
    showError();
  }
  
  // Cleanup
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  
  delay(2000);
  Serial.println(F("--- Ready for next card ---\n"));
}