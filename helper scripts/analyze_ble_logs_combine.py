#!/usr/bin/env python3
import os
import sys
import re
import math

import numpy as np
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
                # flush previous record if complete
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


def summarize_records(records, label=None, expected_count=200):
    """
    Print summary stats for one orientation / file.
    Returns (n_packets, mean_rssi, loss_count, loss_pct)
    """
    if not records:
        print(f"=== Summary: {label} ===")
        print("  No packets found.\n")
        return 0, float("nan"), expected_count, 100.0

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

    return n, mean_rssi, loss_count, loss_pct


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

def plot_combined_rssi_vs_counter(all_sequences, out_path, expected_count=200):
    """
    all_sequences: list of (label, n_packets, x_list, y_list), one per file.
    Creates one continuous RSSI vs 'virtual counter' plot with vertical
    separators between files. Legend labels include "n/expected_count".
    """
    if not all_sequences:
        return

    plt.figure(figsize=(20,12))
    current_offset = 0
    for label, n_packets, x_list, y_list in all_sequences:
        xs = [current_offset + i for i in range(len(x_list))]
        disp_label = f"{label} ({n_packets}/{expected_count})"
        plt.plot(xs, y_list, marker=".", linestyle="-", label=disp_label)
        # vertical separator at the end of this chunk
        if xs:
            plt.axvline(xs[-1], color="gray", linestyle="--", linewidth=0.5)
            current_offset = xs[-1] + 5  # gap before next chunk

    plt.xlabel("Virtual packet index (across files)")
    plt.ylabel("RSSI (dBm)")
    plt.title("Combined RSSI vs Counter (all files)")
    plt.gca().invert_yaxis()
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_hist_grid(hist_data, out_path):
    """
    hist_data: list of (label, rssi_list)
    Make a grid of histograms (bars) with an overlaid dot/line curve.
    """
    if not hist_data:
        return

    n = len(hist_data)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows))
    # Normalize axes to a flat list
    if rows == 1 and cols == 1:
        axes = [axes]
    elif rows == 1 or cols == 1:
        axes = list(axes)
    else:
        axes = [ax for row in axes for ax in row]

    for idx, (label, rssi_vals) in enumerate(hist_data):
        ax = axes[idx]
        # bar histogram
        counts, bins, patches = ax.hist(rssi_vals, bins=20, alpha=0.7)
        # dot/line curve over the same histogram
        bin_centers = (bins[:-1] + bins[1:]) / 2.0
        ax.plot(bin_centers, counts, marker="o", linestyle="-")
        ax.set_title(label)
        ax.set_xlabel("RSSI (dBm)")
        ax.set_ylabel("Count")

    # Remove unused subplots if any
    for j in range(len(hist_data), len(axes)):
        fig.delaxes(axes[j])

    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_mean_rssi_bar(mean_data, out_path, expected_count=200):
    """
    mean_data: list of (label, n_packets, mean_rssi)
    Produces a bar chart of mean RSSI per file.
    """
    if not mean_data:
        return

    labels = [f"{label}\n({n}/{expected_count})" for (label, n, _) in mean_data]
    means = [m for (_, _, m) in mean_data]

    plt.figure(figsize=(max(6, len(labels) * 1.5), 4))
    plt.bar(range(len(labels)), means)
    plt.xticks(range(len(labels)), labels, rotation=30, ha="right")
    plt.ylabel("Mean RSSI (dBm)")
    plt.title("Mean RSSI per file")
    plt.gca().invert_yaxis()  # better dBm visualization
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_loss_and_receive_bars(loss_data, out_path, expected_count=200):
    """
    loss_data: list of (label, loss_count, loss_pct)

    Creates one figure with 2 stacked bar charts:
      - Top: packet loss (%), annotated with "lost/expected"
      - Bottom: packet received (%), annotated with "recv/expected"
    Both axes use 0–100 % on Y.
    """
    if not loss_data:
        return

    labels      = [label for (label, _, _) in loss_data]
    loss_counts = [lc    for (_, lc, _) in loss_data]
    loss_pcts   = [lp    for (_, _, lp) in loss_data]

    recv_counts = [expected_count - lc for lc in loss_counts]
    recv_pcts   = [100.0 - lp for lp in loss_pcts]

    x = np.arange(len(labels))

    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(max(6, len(labels) * 1.5), 8),
        sharex=True
    )

    # --- Top: LOSS ---
    bars1 = ax1.bar(x, loss_pcts)
    ax1.set_ylabel("Packet loss (%)")
    ax1.set_ylim(0, 100)
    ax1.set_title(f"Packet loss per file (out of {expected_count} sent)")

    for i, bar in enumerate(bars1):
        height = bar.get_height()
        text = f"{loss_counts[i]}/{expected_count}\n{loss_pcts[i]:.1f}%"
        ax1.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 1,
            text,
            ha="center",
            va="bottom",
            fontsize=8,
            rotation=90
        )

    # --- Bottom: RECEIVED ---
    bars2 = ax2.bar(x, recv_pcts)
    ax2.set_ylabel("Packets received (%)")
    ax2.set_ylim(0, 100)
    ax2.set_title(f"Packets received per file (out of {expected_count} sent)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=30, ha="right")

    for i, bar in enumerate(bars2):
        height = bar.get_height()
        text = f"{recv_counts[i]}/{expected_count}\n{recv_pcts[i]:.1f}%"
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 1,
            text,
            ha="center",
            va="bottom",
            fontsize=8,
            rotation=90
        )

    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_ble_logs.py file1 file2 ...")
        sys.exit(1)

    log_paths = sys.argv[1:]
    expected_count = 200  # per test we expect 200 packets

    all_line_data = []   # for combined RSSI vs counter: (label, n, xs, ys)
    all_hist_data = []   # for histogram grid: (label, rssi_vals)
    all_mean_data = []   # for mean RSSI bar: (label, n, mean_rssi)
    all_loss_data = []   # for loss bar: (label, loss_count, loss_pct)

    for path in log_paths:
        if not os.path.isfile(path):
            print(f"Warning: {path} not found, skipping.")
            continue

        records = parse_log(path)
        base = os.path.splitext(os.path.basename(path))[0]

        n_packets, mean_rssi, loss_count, loss_pct = summarize_records(
            records, label=base, expected_count=expected_count
        )

        # Per-file plots
        out_line = f"{base}_rssi_vs_counter.png"
        out_hist = f"{base}_rssi_hist.png"
        plot_rssi_vs_counter(records, out_line, title=f"RSSI vs Counter ({base})")
        plot_rssi_hist(records, out_hist, title=f"RSSI Histogram ({base})")
        print(f"  Saved plots: {out_line}, {out_hist}\n")

        if records:
            rec_sorted = sorted(records, key=lambda r: r.get("tx_unix_ms", 0))
            xs = [r["tx_counter"] for r in rec_sorted]
            ys = [r["rssi_dbm"] for r in rec_sorted]

            all_line_data.append((base, n_packets, xs, ys))

            rssi_vals = [r["rssi_dbm"] for r in records if "rssi_dbm" in r]
            if rssi_vals:
                all_hist_data.append((base, rssi_vals))

            if not math.isnan(mean_rssi):
                all_mean_data.append((base, n_packets, mean_rssi))

            all_loss_data.append((base, loss_count, loss_pct))

    # Combined RSSI vs counter across files
    if len(all_line_data) > 1:
        combined_line_path = "combined_rssi_vs_counter.png"
        plot_combined_rssi_vs_counter(all_line_data, combined_line_path, expected_count=expected_count)
        print(f"Combined RSSI vs counter plot saved to: {combined_line_path}")

    # Combined histogram GRID image
    if len(all_hist_data) > 1:
        hist_grid_path = "histograms_grid.png"
        plot_hist_grid(all_hist_data, hist_grid_path)
        print(f"Combined histogram grid saved to: {hist_grid_path}")

    # Mean RSSI bar chart
    if all_mean_data:
        mean_bar_path = "mean_rssi_per_file.png"
        plot_mean_rssi_bar(all_mean_data, mean_bar_path, expected_count=expected_count)
        print(f"Mean RSSI bar chart saved to: {mean_bar_path}")

    # Packet loss + received bar charts (same image)
    if all_loss_data:
        loss_bar_path = "packet_loss_and_received_per_file.png"
        plot_loss_and_receive_bars(all_loss_data, loss_bar_path, expected_count=expected_count)
        print(f"Packet loss & received bar charts saved to: {loss_bar_path}")

    print("Done.")


if __name__ == "__main__":
    main()