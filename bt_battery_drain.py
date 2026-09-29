#!/usr/bin/env python3
"""
bluetooth-battery-discharge-detector
====================================
Poll a Bluetooth LE device's standard Battery Service (0x180F) and compute
its discharge rate (%/hour) plus an estimated time-to-empty.

Usage:
    python bt_battery_drain.py scan
    python bt_battery_drain.py monitor AA:BB:CC:DD:EE:FF [--interval 60] [--duration 3600] [--out log.csv]

Requires: pip install bleak
"""

import argparse
import asyncio
import csv
import sys
import time
from datetime import datetime, timezone

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("The 'bleak' package is required:  pip install bleak")
    sys.exit(1)

BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34f0"


async def cmd_scan(args):
    print("Scanning for BLE devices (10s)...")
    devices = await BleakScanner.discover(timeout=10.0)
    if not devices:
        print("No devices found.")
        return
    print(f"{'Address':<20} {'Name'}")
    print("-" * 50)
    for d in sorted(devices, key=lambda x: (x.name or "zzz")):
        print(f"{d.address:<20} {d.name or '(unknown)'}")


def discharge_rate(samples):
    """Least-squares slope of battery % vs hours. Negative = draining."""
    n = len(samples)
    if n < 2:
        return None
    t0 = samples[0][0]
    xs = [(t - t0) / 3600.0 for t, _ in samples]
    ys = [lvl for _, lvl in samples]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def fmt_eta(hours):
    if hours is None or hours < 0:
        return "--"
    if hours > 999:
        return ">999h"
    h = int(hours)
    m = int((hours - h) * 60)
    return f"{h}h {m:02d}m"


async def cmd_monitor(args):
    address = args.address
    interval = max(5, args.interval)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.out or f"battery_{address.replace(':', '')}_{stamp}.csv"

    samples = []
    csv_file = open(out_path, "w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["timestamp_utc", "battery_pct"])

    print(f"Connecting to {address} ...  (Ctrl+C to stop)")
    try:
        async with BleakClient(address, timeout=20.0) as client:
            if not client.is_connected:
                print("Could not connect. Make sure the device is on, nearby and paired.")
                return
            try:
                raw = await client.read_gatt_char(BATTERY_LEVEL_UUID)
            except Exception as exc:
                print(f"Device does not expose the standard BLE Battery Service: {exc}")
                print("This tool only works with devices exposing Battery Service (0x180F).")
                return
            print(f"Connected. Battery service found. Logging to {out_path}\n")
            print(f"{'Time':<10} {'Level':<8} {'Rate':<14} {'ETA empty'}")
            print("-" * 48)
            stop_at = time.time() + args.duration if args.duration > 0 else None
            while True:
                try:
                    raw = await client.read_gatt_char(BATTERY_LEVEL_UUID)
                    level = int(raw[0])
                except Exception as exc:
                    print(f"Read failed ({exc}); retrying on next tick...")
                    await asyncio.sleep(interval)
                    continue
                now = time.time()
                samples.append((now, level))
                writer.writerow([datetime.fromtimestamp(now, timezone.utc).isoformat(), level])
                csv_file.flush()

                rate = discharge_rate(samples)
                eta = level / -rate if (rate is not None and rate < -0.001) else None
                elapsed = now - samples[0][0]
                rate_str = f"{rate:+.2f} %/h" if (rate is not None and elapsed >= 120) else "collecting..."
                print(f"{datetime.now().strftime('%H:%M:%S'):<10} {level:>3}%     "
                      f"{rate_str:<14} {fmt_eta(eta)}", flush=True)

                if stop_at and now >= stop_at:
                    break
                await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        csv_file.close()

    if len(samples) >= 2:
        rate = discharge_rate(samples)
        span_h = (samples[-1][0] - samples[0][0]) / 3600
        print(f"\nSession: {len(samples)} samples over {span_h:.2f}h "
              f"({samples[0][1]}% -> {samples[-1][1]}%)")
        if rate is not None:
            print(f"Discharge rate: {rate:+.2f} %/hour")
            if rate < -0.001:
                print(f"Estimated time from {samples[-1][1]}% to empty: "
                      f"{fmt_eta(samples[-1][1] / -rate)}")
        print(f"Log saved to {out_path}")


def main():
    p = argparse.ArgumentParser(description="Bluetooth battery discharge rate detector")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Discover nearby BLE devices")
    s.set_defaults(func=cmd_scan)

    m = sub.add_parser("monitor", help="Monitor a device's battery level")
    m.add_argument("address", help="BLE address, e.g. AA:BB:CC:DD:EE:FF")
    m.add_argument("--interval", type=int, default=60,
                   help="Seconds between reads (default 60, min 5)")
    m.add_argument("--duration", type=int, default=0,
                   help="Stop after N seconds (0 = run forever)")
    m.add_argument("--out", default=None, help="CSV log path")
    m.set_defaults(func=cmd_monitor)

    args = p.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
