#!/usr/bin/env python3
import os
import sys
import re
import math

import matplotlib.pyplot as plt


def parse_log(path):
    """
    Parse a single ESP32 log file.
    Returns a list of dicts with fields:
      file, scan_cycle, rx_unix_ms, tx_counter, tx_unix_ms, delta_ms, rssi_dbm
    """
    records = []
    scan_cycle = None
    current = None

    scan_start_re = re.compile(r"=== Scan cycle #(\d+) START")
    rx_unix_re = re.compile(r"RX Unix ms \(ESP32\):\s*([0-9]+)")
    tx_counter_re = re.compile(r"TX counter \(payload\):\s*([0-9]+)")
    tx_unix_re = re.compile(r"TX Unix ms \(payload\):\s*([0-9]+)")
    delta_re = re.compile(r"Delta = .*:\s*([-0-9]+)\s+ms")
    rssi_re = re.compile(r"RSSI:\s*([-0-9]+)\s*dBm")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Scan cycle markers
            m = scan_start_re.match(line)
            if m:
                scan_cycle = int(m.group(1))
                continue

            # Start of a record
            if line.startswith("=== TARGET BLE DEVICE DETECTED ==="):
                # If previous current is complete but not flushed, flush it
                if current and "rssi_dbm" in current:
                    current["file"] = os.path.basename(path)
                    current["scan_cycle"] = scan_cycle
                    records.append(current)

                current = {
                    "scan_cycle": scan_cycle,
                }
                continue

            if current is None:
                continue  # ignore lines outside a packet block

            # RX Unix
            m = rx_unix_re.match(line)
            if m:
                current["rx_unix_ms"] = int(m.group(1))
                continue

            # TX counter
            m = tx_counter_re.match(line)
            if m:
                current["tx_counter"] = int(m.group(1))
                continue

            # TX Unix
            m = tx_unix_re.match(line)
            if m:
                current["tx_unix_ms"] = int(m.group(1))
                continue

            # Delta
            m = delta_re.match(line)
            if m:
                current["delta_ms"] = int(m.group(1))
                continue

            # RSSI
            m = rssi_re.match(line)
            if m:
                current["rssi_dbm"] = int(m.group(1))
                continue

            # Empty line often means end of block – flush if record looks complete
            if line == "" and "rssi_dbm" in current:
                current["file"] = os.path.basename(path)
                current["scan_cycle"] = scan_cycle
                records.append(current)
                current = None

    # Flush last record if needed
    if current and "rssi_dbm" in current:
        current["file"] = os.path.basename(path)
        current["scan_cycle"] = scan_cycle
        records.append(current)

    return records


def summarize_records(records, label=None):
    """
    Print summary stats for one orientation / file.
    """
    if not records:
        print(f"[{label}] No packets found.")
        return

    # Sort by TX time for consistent order
    records_sorted = sorted(records, key=lambda r: r.get("tx_unix_ms", 0))

    rssi_vals = [r["rssi_dbm"] for r in records_sorted if "rssi_dbm" in r]
    delta_vals = [r["delta_ms"] for r in records_sorted if "delta_ms" in r]
    counters = [r["tx_counter"] for r in records_sorted if "tx_counter" in r]

    n = len(records_sorted)
    mean_rssi = sum(rssi_vals) / len(rssi_vals) if rssi_vals else float("nan")
    min_rssi = min(rssi_vals) if rssi_vals else None
    max_rssi = max(rssi_vals) if rssi_vals else None

    mean_delta = sum(delta_vals) / len(delta_vals) if delta_vals else float("nan")
    min_delta = min(delta_vals) if delta_vals else None
    max_delta = max(delta_vals) if delta_vals else None

    first_counter = counters[0]
    last_counter = counters[-1]

    # Handle potential 16-bit wrap-around just in case
    expected_span = (last_counter - first_counter) & 0xFFFF
    # expected_count = expected_span + 1
    expected_count = 200 # per test kita bikin 200 paket
    loss_count = max(expected_count - n, 0)
    loss_pct = (loss_count / expected_count * 100.0) if expected_count > 0 else float("nan")

    first_tx = records_sorted[0].get("tx_unix_ms")
    last_tx = records_sorted[-1].get("tx_unix_ms")
    duration_s = (last_tx - first_tx) / 1000.0 if first_tx and last_tx else float("nan")

    print(f"=== Summary: {label} ===")
    print(f"  Packets:           {n}")
    print(f"  Counter range:     {first_counter} -> {last_counter} (expected ~{expected_count})")
    print(f"  Approx loss:       {loss_count} packets (~{loss_pct:.2f}%)")
    print(f"  TX time span:      {duration_s:.2f} s")
    print(f"  RSSI mean/min/max: {mean_rssi:.2f} dBm / {min_rssi} / {max_rssi}")
    print(f"  Δ (RX-TX) mean/min/max: {mean_delta:.2f} ms / {min_delta} / {max_delta}")
    print()


def plot_rssi_vs_counter(records, out_path, title=None):
    if not records:
        return

    records_sorted = sorted(records, key=lambda r: r.get("tx_unix_ms", 0))
    x = [r["tx_counter"] for r in records_sorted]
    y = [r["rssi_dbm"] for r in records_sorted]

    plt.figure()
    plt.plot(x, y, marker=".", linestyle="-")
    plt.xlabel("TX counter")
    plt.ylabel("RSSI (dBm)")
    if title:
        plt.title(title)
    plt.gca().invert_yaxis()  # so -40 appears "higher" than -80
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_rssi_hist(records, out_path, title=None):
    if not records:
        return

    y = [r["rssi_dbm"] for r in records]

    plt.figure()
    plt.hist(y, bins=20)
    plt.xlabel("RSSI (dBm)")
    plt.ylabel("Count")
    if title:
        plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_ble_logs.py +X1 -X1 +Y1 -Y1 +Z1 -Z1")
        sys.exit(1)

    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"Warning: {path} not found, skipping.")
            continue

        records = parse_log(path)
        base = os.path.splitext(os.path.basename(path))[0]

        summarize_records(records, label=base)

        # Generate plots
        out_line = f"{base}_rssi_vs_counter.png"
        out_hist = f"{base}_rssi_hist.png"
        plot_rssi_vs_counter(records, out_line, title=f"RSSI vs Counter ({base})")
        plot_rssi_hist(records, out_hist, title=f"RSSI Histogram ({base})")
        print(f"  Saved plots: {out_line}, {out_hist}")
        print()

    print("Done.")


if __name__ == "__main__":
    main()
