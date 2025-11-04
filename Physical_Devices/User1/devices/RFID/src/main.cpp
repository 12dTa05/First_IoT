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
unsigned long lastHeartbeat = 0;
const unsigned long HEARTBEAT_INTERVAL = 60000; // 60 seconds

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

struct RemoteControlState {
    bool listening_for_command = false;
    unsigned long listen_start = 0;
    const unsigned long listen_timeout = 30000;  // 30 seconds
    String current_command_id = "";
    String initiated_by = "";
};

RemoteControlState remoteCtrl;

// ============= FORWARD DECLARATIONS =============
void handleRemoteUnlockCommand(String command);
void handleRemoteLockCommand(String command);
void executeRemoteUnlock(unsigned long duration_ms);
void sendRemoteResponse(String command_id, bool success, const char* status);

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

  Serial.printf("[WAIT] Waiting for ACK (timeout: %lu ms)...\n", timeoutMs);

  while (millis() - startTime < timeoutMs) {
    if (lora.available() > 0) {
      Serial.println(F("[WAIT] LoRa data available!"));

      ResponseContainer rsc = lora.receiveMessage();

      Serial.printf("[WAIT] RX: status=%d, len=%d\n", rsc.status.code, rsc.data.length());

      if (rsc.status.code != 1) {
        Serial.printf("[WAIT] Invalid status code: %d\n", rsc.status.code);
        continue;
      }

      if (rsc.data.length() < 7) {
        Serial.printf("[WAIT] Packet too short: %d bytes (need >= 7)\n", rsc.data.length());
        continue;
      }
      
      const uint8_t* buffer = (const uint8_t*)rsc.data.c_str();
      int len = rsc.data.length();

      // Print raw packet
      Serial.print(F("[WAIT] Raw RX packet: "));
      for (int i = 0; i < min(len, 20); i++) {
        Serial.printf("%02X ", buffer[i]);
      }
      Serial.println();

      // Verify header: 0xC0 0x00 0x00
      if (buffer[0] != 0xC0 || buffer[1] != 0x00 || buffer[2] != 0x00) {
        Serial.printf("[WAIT] Invalid header: %02X %02X %02X (expected C0 00 00)\n",
                      buffer[0], buffer[1], buffer[2]);
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

bool checkForRemoteCommand() {
    if (!lora.available()) {
        return false;
    }

    Serial.println(F("[DEBUG] LoRa data available"));

    ResponseContainer rsc = lora.receiveMessage();

    Serial.printf("[DEBUG] LoRa RX: status=%d, len=%d\n", rsc.status.code, rsc.data.length());

    if (rsc.status.code != 1) {
        Serial.printf("[DEBUG] Invalid status code: %d\n", rsc.status.code);
        return false;
    }

    if (rsc.data.length() < 7) {
        Serial.printf("[DEBUG] Data too short: %d bytes (need >= 7)\n", rsc.data.length());
        return false;
    }

    const uint8_t* buffer = (const uint8_t*)rsc.data.c_str();
    int len = rsc.data.length();

    // Print raw packet
    Serial.print(F("[DEBUG] Raw packet: "));
    for (int i = 0; i < min(len, 20); i++) {
        Serial.printf("%02X ", buffer[i]);
    }
    Serial.println();

    // Verify header: 0xC0 0x00 0x00
    if (buffer[0] != 0xC0 || buffer[1] != 0x00 || buffer[2] != 0x00) {
        Serial.printf("[DEBUG] Invalid header: %02X %02X %02X (expected C0 00 00)\n",
                      buffer[0], buffer[1], buffer[2]);
        return false;
    }
    
    // Parse command type at position 7
    uint8_t commandLen = buffer[6];
    if (len != 7 + commandLen) {
        return false;
    }
    
    // Extract command string
    String command = "";
    for (uint8_t i = 0; i < commandLen; i++) {
        command += (char)buffer[7 + i];
    }
    
    Serial.print(F("[REMOTE] Received LoRa command: "));
    Serial.println(command);
    
    // Parse command format: "REMOTE_UNLOCK:{command_id}:{user}:{duration}"
    if (command.startsWith("REMOTE_UNLOCK:")) {
        handleRemoteUnlockCommand(command);
        return true;
    }
    
    if (command.startsWith("REMOTE_LOCK:")) {
        handleRemoteLockCommand(command);
        return true;
    }
    
    // Legacy commands
    if (command == "GRANT") {
        return true;  // Normal RFID grant
    }
    
    if (command == "DENY5") {
        return true;  // Normal RFID deny
    }
    
    return false;
}

// ============= HARDWARE CONTROL =============
void openGate() {
  Serial.println(F("=== ACCESS GRANTED ==="));
  
  gate.write(90);
  sendStatusMessage("open");
  
  delay(5000);
  
  gate.write(0);
  sendStatusMessage("clos");
  
  Serial.println(F("Gate closed"));
}

void handleRemoteUnlockCommand(String command) {
    Serial.println(F("\n[REMOTE] Processing remote unlock command"));
    
    // Parse command format: REMOTE_UNLOCK:{command_id}:{user}:{duration_ms}
    int idx1 = command.indexOf(':');
    int idx2 = command.indexOf(':', idx1 + 1);
    int idx3 = command.indexOf(':', idx2 + 1);
    
    if (idx1 == -1 || idx2 == -1 || idx3 == -1) {
        Serial.println(F("[ERROR] Invalid command format"));
        sendRemoteResponse("error", false, "invalid_format");
        return;
    }
    
    String command_id = command.substring(idx1 + 1, idx2);
    String user = command.substring(idx2 + 1, idx3);
    unsigned long duration_ms = command.substring(idx3 + 1).toInt();
    
    // Validate duration (1-30 seconds)
    if (duration_ms < 1000 || duration_ms > 30000) {
        duration_ms = 5000;  // Default 5 seconds
    }
    
    Serial.printf("[REMOTE] Command ID: %s\n", command_id.c_str());
    Serial.printf("[REMOTE] User: %s\n", user.c_str());
    Serial.printf("[REMOTE] Duration: %lu ms\n", duration_ms);
    
    // Store remote control state
    remoteCtrl.current_command_id = command_id;
    remoteCtrl.initiated_by = user;
    
    // Execute unlock
    executeRemoteUnlock(duration_ms);
    
    // Send acknowledgment
    sendRemoteResponse(command_id, true, "unlocked");
    
    // Send status message via LoRa
    sendStatusMessage("REMOTE_OPEN");
}

// ============= HANDLE REMOTE LOCK COMMAND =============
void handleRemoteLockCommand(String command) {
    Serial.println(F("\n[REMOTE] Processing remote lock command"));
    
    // Parse command format: REMOTE_LOCK:{command_id}:{user}
    int idx1 = command.indexOf(':');
    int idx2 = command.indexOf(':', idx1 + 1);
    
    if (idx1 == -1 || idx2 == -1) {
        Serial.println(F("[ERROR] Invalid command format"));
        return;
    }
    
    String command_id = command.substring(idx1 + 1, idx2);
    String user = command.substring(idx2 + 1);
    
    Serial.printf("[REMOTE] Lock by: %s\n", user.c_str());
    
    // Execute lock immediately
    gate.write(0);
    
    // Send acknowledgment
    sendRemoteResponse(command_id, true, "locked");
    
    // Send status
    sendStatusMessage("REMOTE_CLOS");
    
    Serial.println(F("[REMOTE] Gate locked"));
}

// ============= EXECUTE REMOTE UNLOCK =============
void executeRemoteUnlock(unsigned long duration_ms) {
    Serial.println(F("\n=== REMOTE ACCESS GRANTED ==="));
    
    // Open gate
    gate.write(90);
    delay(500);
    
    Serial.printf("Gate opened for %lu ms\n", duration_ms);
    
    // Wait for specified duration
    delay(duration_ms);
    
    // Close gate
    gate.write(0);
    delay(500);
    
    sendStatusMessage("AUTO_CLOS");
    
    Serial.println(F("Gate closed automatically"));
    Serial.println(F("=== REMOTE UNLOCK COMPLETE ===\n"));
}

// ============= SEND REMOTE RESPONSE VIA LORA =============
void sendRemoteResponse(String command_id, bool success, const char* status) {
    // Create response message
    // Format: "ACK:{command_id}:{success}:{status}"
    String response = "ACK:" + command_id + ":" + 
                     String(success ? "1" : "0") + ":" + 
                     String(status);
    
    uint8_t buffer[64];
    int idx = 0;
    
    // Header
    buffer[idx++] = 0xC0;
    buffer[idx++] = 0x00;
    buffer[idx++] = 0x00;
    
    // Address (Gateway)
    buffer[idx++] = 0x00;
    buffer[idx++] = 0x00;
    
    // Channel
    buffer[idx++] = 23;
    
    // Length
    buffer[idx++] = response.length();
    
    // Data
    for (unsigned int i = 0; i < response.length(); i++) {
        buffer[idx++] = response[i];
    }
    
    // Send via LoRa
    lora.sendMessage(buffer, idx);
    
    Serial.print(F("[RESPONSE] Sent: "));
    Serial.println(response);
}

// ============= HEARTBEAT =============
void sendHeartbeat() {
  sendStatusMessage("ALIVE");
  Serial.println(F("[HEARTBEAT] Sent to Gateway"));
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

  remoteCtrl.listening_for_command = false;
  remoteCtrl.listen_start = 0;

  // Send online status
  sendStatusMessage("ONLINE");
  lastHeartbeat = millis();

  Serial.println(F("\n[READY] Waiting for RFID cards...\n"));
  
}

// ============= MAIN LOOP =============
void loop() {
  // Send periodic heartbeat
  unsigned long currentMillis = millis();
  if (currentMillis - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    sendHeartbeat();
    lastHeartbeat = currentMillis;
  }

  if (checkForRemoteCommand()) {
    delay(100);
  }

  // Check for RFID card
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }
  
  Serial.println(F("\n--- RFID Card Detected ---"));
  
  // Validate UID
  if (rfid.uid.size == 0 || rfid.uid.size > 10) {
    Serial.println(F("[ERROR] Invalid UID size"));
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
    rfid.PICC_HaltA();
    rfid.PCD_StopCrypto1();
    delay(2000);
    return;
  }

  // CRITICAL: Delay to allow LoRa module to switch from TX to RX mode
  // Without this delay, the module is still in TX mode and cannot receive Gateway response
  Serial.println(F("[WAIT] Switching LoRa to RX mode..."));
  delay(100);  // 100ms delay for TX->RX transition

  // Wait for ACK from Gateway
  bool accessGranted = false;
  if (receiveAckMessage(&accessGranted, RESPONSE_TIMEOUT_MS)) {
    if (accessGranted) {
      openGate();
    } 
  } 
  else {
    Serial.println(F("[ERROR] No response from Gateway"));
  }
  
  // Cleanup
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  
  delay(2000);
  Serial.println(F("--- Ready for next card ---\n"));
}