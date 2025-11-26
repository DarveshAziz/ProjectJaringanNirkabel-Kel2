#!/usr/bin/env python3
import os
import sys
import re
import csv

TX_DEVICE_NAME = "A72Aziz"   # always set to this


def parse_log(path):
    """
    Parse a single ESP32 log file.
    Returns list of dicts with:
      tx_unix_ms(phone), rx_unix_ms(esp32), payload_counter, delta_ms, rssi_dbm, tx_device_name
    """
    records = []
    current = None

    # Regex patterns
    rx_unix_re = re.compile(r"RX Unix ms \(ESP32\):\s*([0-9]+)")
    tx_counter_re = re.compile(r"TX counter \(payload\):\s*([0-9]+)")
    tx_unix_re = re.compile(r"TX Unix ms \(payload\):\s*([0-9]+)")
    delta_re = re.compile(r"Delta = .*:\s*([-0-9]+)\s+ms")
    rssi_re = re.compile(r"RSSI:\s*([-0-9]+)\s*dBm")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Start of a packet block
            if line.startswith("=== TARGET BLE DEVICE DETECTED ==="):
                if current and "rssi_dbm" in current:
                    current["tx_device_name"] = TX_DEVICE_NAME
                    records.append(current)
                current = {}
                continue

            if current is None:
                continue

            # Extract fields
            if (m := rx_unix_re.match(line)):
                current["rx_unix_ms(esp32)"] = int(m.group(1))
                continue

            if (m := tx_counter_re.match(line)):
                current["payload_counter"] = int(m.group(1))
                continue

            if (m := tx_unix_re.match(line)):
                current["tx_unix_ms(phone)"] = int(m.group(1))
                continue

            if (m := delta_re.match(line)):
                current["delta_ms"] = int(m.group(1))
                continue

            if (m := rssi_re.match(line)):
                current["rssi_dbm"] = int(m.group(1))
                continue

            # End of block
            if line == "" and "rssi_dbm" in current:
                current["tx_device_name"] = TX_DEVICE_NAME
                records.append(current)
                current = None

    # Flush last packet
    if current and "rssi_dbm" in current:
        current["tx_device_name"] = TX_DEVICE_NAME
        records.append(current)

    return records


def write_csv(records, out_path):
    if not records:
        print(f"[WARN] No records for {out_path}. Skipping...")
        return

    header = [
        "tx_unix_ms(phone)",
        "rx_unix_ms(esp32)",
        "payload_counter",
        "delta_ms",
        "rssi_dbm",
        "tx_device_name"
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()

        for r in records:
            row = {col: r.get(col, "") for col in header}
            writer.writerow(row)

    print(f"[OK] CSV saved: {out_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python ble_logs_to_csv.py log1 log2 ...")
        sys.exit(1)

    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"Skipping missing file: {path}")
            continue

        records = parse_log(path)
        base = os.path.splitext(os.path.basename(path))[0]
        csv_path = f"{base}.csv"

        write_csv(records, csv_path)

    print("Done.")


if __name__ == "__main__":
    main()