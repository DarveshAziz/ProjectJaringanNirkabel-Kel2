#!/usr/bin/env python3
import argparse
import re
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
import serial

COUNTER_RE = re.compile(r"TX counter \(payload\):\s*([0-9]+)")
RSSI_RE    = re.compile(r"RSSI:\s*([-0-9]+)\s*dBm")


def serial_reader(port, baud, max_points, data_lock, counters, rssis, stop_flag):
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=1.0)
        print(f"[INFO] Opened serial port {port} @ {baud} baud")
    except Exception as e:
        print(f"[ERROR] Could not open {port}: {e}")
        stop_flag["stop"] = True
        return

    current_counter = None
    current_rssi = None

    while not stop_flag["stop"]:
        try:
            line_bytes = ser.readline()
            if not line_bytes:
                continue

            line = line_bytes.decode(errors="ignore").strip()

            m_c = COUNTER_RE.search(line)
            if m_c:
                current_counter = int(m_c.group(1))

            m_r = RSSI_RE.search(line)
            if m_r:
                current_rssi = int(m_r.group(1))

            if current_counter is not None and current_rssi is not None:
                with data_lock:
                    counters.append(current_counter)
                    rssis.append(current_rssi)
                    if len(counters) > max_points:
                        counters.popleft()
                        rssis.popleft()
                current_counter = None
                current_rssi = None

        except Exception as e:
            print(f"[WARN] Serial read error: {e}")
            time.sleep(0.1)

    ser.close()
    print("[INFO] Serial reader stopped.")


def main():
    parser = argparse.ArgumentParser(description="Live RSSI vs Counter plot from ESP32 serial.")
    parser.add_argument("--port", required=True, help="Serial port (e.g., COM9 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--max-points", type=int, default=500,
                        help="Max number of points to keep in the live plot (default: 500)")
    args = parser.parse_args()

    data_lock = threading.Lock()
    counters = deque()
    rssis = deque()
    stop_flag = {"stop": False}

    t = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, args.max_points, data_lock, counters, rssis, stop_flag),
        daemon=True,
    )
    t.start()

    plt.ion()
    fig, ax = plt.subplots(figsize=(8, 5))
    line, = ax.plot([], [], marker=".", linestyle="-")
    ax.set_xlabel("TX counter (payload)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("Live RSSI vs Counter")
    ax.grid(True)

    # Invert Y **once**, outside the loop
    ax.invert_yaxis()

    try:
        while True:
            with data_lock:
                x = list(counters)
                y = list(rssis)

            if x:
                line.set_data(x, y)

                # Recalculate limits but keep orientation
                ymin = min(y) - 2
                ymax = max(y) + 2
                ax.set_xlim(min(x), max(x) + 1)
                ax.set_ylim(ymax, ymin)  # top, bottom (already inverted)

            fig.canvas.draw()
            fig.canvas.flush_events()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C detected, stopping...")
    finally:
        stop_flag["stop"] = True
        t.join(timeout=2.0)
        plt.ioff()
        plt.close(fig)


if __name__ == "__main__":
    main()
