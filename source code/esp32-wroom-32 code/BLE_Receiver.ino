#include <WiFi.h>
#include "time.h"
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

#define LED_PIN   2
#define SCAN_TIME 10   // seconds

// EXACT name used by your advertiser app
String TARGET_NAME = "A72Aziz";

// ====== WiFi + NTP config (fill in SSID/PASS) ======
const char* WIFI_SSID = "josh";
const char* WIFI_PASS = "asdfzxcv";

// UTC time (no timezone offset, you can adjust later if needed)
const long  GMT_OFFSET_SEC = 0;
const int   DAYLIGHT_OFFSET_SEC = 0;
const char* NTP_SERVER = "pool.ntp.org";

BLEScan* pBLEScan;

// Count how many scan cycles we’ve done
unsigned long scanCycle = 0;

/* -------- Helper: connect WiFi + sync time -------- */

void connectWiFiAndSyncTime() {
  Serial.print("Connecting to WiFi ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi connect failed, time sync will not work!");
    return;
  }

  Serial.print("WiFi connected. IP: ");
  Serial.println(WiFi.localIP());

  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);

  Serial.print("Waiting for NTP time");
  time_t now = 0;
  int retries = 0;
  while (now < 1700000000 && retries < 30) { // 1700000000 ~ 2023-11-ish
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    retries++;
  }
  Serial.println();

  if (now < 1700000000) {
    Serial.println("NTP sync failed, time may be wrong.");
  } else {
    struct tm timeinfo;
    localtime_r(&now, &timeinfo);
    Serial.print("Time synced. Current UTC: ");
    Serial.println(asctime(&timeinfo));
  }
}

/* -------- Helpers to parse AD structures ---------- */

// Decode Tx Power AD (0x0A)
int8_t decodeTxPowerAD(uint8_t* raw, int len) {
  int i = 0;
  while (i < len) {
    uint8_t fieldLen = raw[i];
    if (fieldLen == 0) break;
    if (i + fieldLen >= len) break;

    uint8_t type = raw[i + 1];
    uint8_t* data = &raw[i + 2];
    uint8_t dataLen = fieldLen - 1;

    if (type == 0x0A && dataLen >= 1) {
      return (int8_t)data[0];
    }

    i += fieldLen + 1;
  }
  return 127;
}

/*
   Decode our custom Manufacturer Data payload (11 bytes payload after companyID).

   Android side:

   val payloadBuf = ByteBuffer.allocate(2 + 1 + 8)
   payloadBuf.putShort(counter.toShort())      // [0-1]
   payloadBuf.put(txPowerLevel.toByte())       // [2]
   payloadBuf.putLong(txUnixMs)                // [3-10], big-endian

   Manufacturer AD structure:

   [len] [0xFF] [companyID_L] [companyID_H] [payload...]

   where companyID = 0xFFFF => companyID_L=0xFF, companyID_H=0xFF.
*/

bool decodeManufacturerPayload(
  uint8_t* raw, int len,
  uint16_t &counter,
  int8_t &txPowerLevelCode,
  uint64_t &txUnixMs
) {
  int i = 0;
  while (i < len) {
    uint8_t fieldLen = raw[i];
    if (fieldLen == 0) break;
    if (i + fieldLen >= len) break;

    uint8_t type = raw[i + 1];
    uint8_t* data = &raw[i + 2];
    uint8_t dataLen = fieldLen - 1;

    // Need at least companyID (2) + payload (11) = 13 bytes
    if (type == 0xFF && dataLen >= 2 + 11) {
      uint8_t companyL = data[0];
      uint8_t companyH = data[1];
      if (companyL == 0xFF && companyH == 0xFF) {
        uint8_t* p = &data[2];

        // counter: 2 bytes big-endian
        counter = (uint16_t(p[0]) << 8) | uint16_t(p[1]);

        // txPowerLevel code: 1 byte
        txPowerLevelCode = (int8_t)p[2];

        // txUnixMs: 8 bytes big-endian
        txUnixMs =
          (uint64_t(p[3]) << 56) |
          (uint64_t(p[4]) << 48) |
          (uint64_t(p[5]) << 40) |
          (uint64_t(p[6]) << 32) |
          (uint64_t(p[7]) << 24) |
          (uint64_t(p[8]) << 16) |
          (uint64_t(p[9]) << 8)  |
           uint64_t(p[10]);

        return true;
      }
    }

    i += fieldLen + 1;
  }

  return false;
}

/* -------- Callback -------- */

class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {

    // RX Unix time on ESP32 from NTP-synced RTC
    time_t nowSec = time(nullptr);
    uint64_t rxUnixMs = (uint64_t)nowSec * 1000ULL;
    unsigned long rxMicros = micros();
    rxUnixMs += (rxMicros % 1000000UL) / 1000UL;  // ~1ms extra resolution

    if (!advertisedDevice.haveName()) return;

    String name = advertisedDevice.getName().c_str();
    if (name != TARGET_NAME) return;   // only your phone

    int rssi = advertisedDevice.getRSSI();
    uint8_t* raw = advertisedDevice.getPayload();
    int len = advertisedDevice.getPayloadLength();

    uint16_t counter = 0;
    int8_t txPowerCodePayload = 0;
    uint64_t txUnixMs = 0;
    bool hasManuf = decodeManufacturerPayload(
      raw, len, counter, txPowerCodePayload, txUnixMs
    );

    int8_t txPowerAD = decodeTxPowerAD(raw, len);

    Serial.println("=== TARGET BLE DEVICE DETECTED ===");

    // ---- RX Unix time ----
    Serial.print("RX Unix ms (ESP32): ");
    Serial.println((unsigned long long)rxUnixMs);

    if (hasManuf) {
      Serial.print("TX counter (payload): ");
      Serial.println(counter);

      Serial.print("TX Unix ms (payload): ");
      Serial.println((unsigned long long)txUnixMs);

      long long deltaMs = (long long)rxUnixMs - (long long)txUnixMs;
      Serial.print("Delta = RX_unix_ms - TX_unix_ms: ");
      Serial.print(deltaMs);
      Serial.println(" ms");
    } else {
      Serial.println("Manufacturer payload: not found / parse error");
    }

    // ---- RSSI ----
    Serial.print("RSSI: ");
    Serial.print(rssi);
    Serial.println(" dBm");

    // ---- Raw bytes (HEX) ----
    Serial.print("Raw AD Payload (hex): ");
    for (int i = 0; i < len; i++) {
      if (raw[i] < 16) Serial.print("0");
      Serial.print(raw[i], HEX);
      Serial.print(" ");
    }
    Serial.println();

    // ---- Raw bytes (ASCII-ish) ----
    Serial.print("Raw AD Payload (ASCII): ");
    for (int i = 0; i < len; i++) {
      char c = raw[i];
      if (c >= 32 && c <= 126) Serial.print(c);
      else Serial.print(".");
    }
    Serial.println();

    // ---- Tx Power (AD 0x0A) ----
    if (txPowerAD != 127) {
      Serial.print("Tx Power (AD 0x0A): ");
      Serial.print(txPowerAD);
      Serial.println(" dBm");
    } else {
      Serial.println("Tx Power (AD 0x0A): N/A");
    }

    // ---- Tx power code from payload (0..3) ----
    if (hasManuf) {
      Serial.print("Tx Power level code (payload): ");
      Serial.print(txPowerCodePayload);
      Serial.println("  (0=ULTRA_LOW,1=LOW,2=MEDIUM,3=HIGH)");
    }

    // Blink on every detected packet from your phone
    digitalWrite(LED_PIN, HIGH);
    delay(80);
    digitalWrite(LED_PIN, LOW);

    Serial.println();
  }
};

/* -------- Setup / Loop -------- */

void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.println("Starting BLE Range-Test Scanner (TX/RX Unix timestamps, packet#, raw bytes, Tx power)...");
  Serial.print("Target name = ");
  Serial.println(TARGET_NAME);

  connectWiFiAndSyncTime();

  BLEDevice::init("");
  pBLEScan = BLEDevice::getScan();
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());
  pBLEScan->setActiveScan(true);
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(100);
}

void loop() {
  // New scan window starting
  scanCycle++;
  unsigned long scanStartMs = millis();

  Serial.println();
  Serial.print("=== Scan cycle #");
  Serial.print(scanCycle);
  Serial.print(" START (SCAN_TIME = ");
  Serial.print(SCAN_TIME);
  Serial.println(" s) ===");

  // Blocking scan for SCAN_TIME seconds
  pBLEScan->start(SCAN_TIME, false);

  unsigned long scanEndMs = millis();
  unsigned long durationMs = scanEndMs - scanStartMs;

  Serial.print("=== Scan cycle #");
  Serial.print(scanCycle);
  Serial.print(" END (elapsed ~");
  Serial.print(durationMs);
  Serial.println(" ms) ===");
  Serial.println();

  // Clear results so RAM doesn’t fill up over many cycles
  pBLEScan->clearResults();
}