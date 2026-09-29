#!/usr/bin/env python3
"""
bluetooth-battery-discharge-detector
====================================
Poll a Bluetooth LE device's standard Battery Service (0x180F) and compute
its discharge rate (%/hour) plus an estimated time-to-empty.

Also detects the device type via the BLE Appearance characteristic (0x2A01),
falling back to advertised service UUIDs and name heuristics.

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
APPEARANCE_UUID = "00002a01-0000-1000-8000-00805f9b34f0"
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34f0"

# ---------------------------------------------------------------------------
# Device-type detection
# ---------------------------------------------------------------------------
# Bluetooth SIG Appearance categories (64-value blocks) and notable sub-types.
APPEARANCE_CATEGORIES = {
    0x0040: "Phone",
    0x0080: "Computer",
    0x00C0: "Watch",
    0x0100: "Clock",
    0x0140: "Display",
    0x0180: "Remote Control",
    0x01C0: "Eye-glasses",
    0x0200: "Tag",
    0x0240: "Keyring",
    0x0280: "Media Player",
    0x02C0: "Barcode Scanner",
    0x0300: "Thermometer",
    0x0340: "Heart Rate Sensor",
    0x0380: "Blood Pressure Monitor",
    0x03C0: "HID Device",
    0x0400: "Glucose Meter",
    0x0440: "Running/Walking Sensor",
    0x0480: "Cycling Device",
    0x04C0: "Control Device",
    0x0500: "Network Device",
    0x0540: "Sensor",
    0x0580: "Light Fixture",
    0x05C0: "Fan",
    0x0600: "HVAC",
    0x0640: "Air Conditioning",
    0x0680: "Humidifier",
    0x06C0: "Heating",
    0x0700: "Access Control",
    0x0740: "Motorized Device",
    0x0780: "Power Device",
    0x07C0: "Light Source",
    0x0800: "Window Covering",
    0x0840: "Audio Sink",
    0x0880: "Audio Source",
    0x08C0: "Motorized Vehicle",
    0x0900: "Domestic Appliance",
    0x0940: "Wearable Audio Device",
    0x0980: "Aircraft",
    0x09C0: "AV Equipment",
    0x0A00: "Display Equipment",
    0x0A40: "Hearing Aid",
    0x0A80: "Gaming Device",
    0x0AC0: "Signage",
}

APPEARANCE_SUBTYPES = {
    0x00C1: "Sports Watch",
    0x00C2: "Smartwatch",
    0x03C1: "Keyboard",
    0x03C2: "Mouse",
    0x03C3: "Joystick",
    0x03C4: "Gamepad",
    0x03C5: "Digitizer Tablet",
    0x03C6: "Card Reader",
    0x03C7: "Digital Pen",
    0x03C8: "Barcode Scanner",
    0x03C9: "Touchpad",
    0x03CA: "Presentation Remote",
    0x0841: "Standalone Speaker",
    0x0842: "Soundbar",
    0x0843: "Bookshelf Speaker",
    0x0844: "Standmounted Speaker",
    0x0845: "Speakerphone",
    0x0881: "Microphone",
    0x0882: "Alarm",
    0x0885: "Broadcasting Device",
    0x0941: "Earbud",
    0x0942: "Headset",
    0x0943: "Headphones",
    0x0944: "Neck Band",
    0x0A41: "In-Ear Hearing Aid",
    0x0A42: "Behind-Ear Hearing Aid",
    0x0A43: "Cochlear Implant",
}

# Advertised GATT service -> device hint (16-bit UUIDs).
SERVICE_HINTS = {
    0x1812: "HID Device",
    0x180D: "Heart Rate Monitor",
    0x1810: "Blood Pressure Monitor",
    0x1808: "Glucose Meter",
    0x1814: "Running/Walking Sensor",
    0x1816: "Cycling Sensor",
    0x1818: "Cycling Power Meter",
    0x181A: "Environmental Sensor",
    0x1822: "Pulse Oximeter",
    0x1826: "Fitness Machine",
}

# Name keyword -> device hint (checked in order; specific first).
NAME_HINTS = [
    ("airpod", "Earbuds"),
    ("bud", "Earbuds"),
    ("headphone", "Headphones"),
    ("headset", "Headset"),
    ("soundbar", "Soundbar"),
    ("speaker", "Speaker"),
    ("keyboard", "Keyboard"),
    ("mouse", "Mouse"),
    ("smartwatch", "Smartwatch"),
    ("watch", "Watch"),
    ("fitness", "Fitness Band"),
    ("band", "Fitness Band"),
    ("tablet", "Tablet"),
    ("laptop", "Computer"),
    ("phone", "Phone"),
    ("tv", "TV"),
]


def _uuid16(uuid_str):
    """Extract the 16-bit short UUID from a 128-bit UUID string."""
    try:
        return int(str(uuid_str).split("-")[0], 16) & 0xFFFF
    except (ValueError, AttributeError, IndexError):
        return None


def appearance_name(value):
    """Human-readable label for a BLE Appearance value."""
    if value in APPEARANCE_SUBTYPES:
        return APPEARANCE_SUBTYPES[value]
    if value in APPEARANCE_CATEGORIES:
        return APPEARANCE_CATEGORIES[value]
    category = value & 0xFFC0
    if category in APPEARANCE_CATEGORIES:
        return f"{APPEARANCE_CATEGORIES[category]} (0x{value:04X})"
    if value == 0:
        return "Unknown"
    return f"Unknown (0x{value:04X})"


def classify_device(name=None, service_uuids=(), appearance=None):
    """Best-effort device-type label.

    Priority: Appearance characteristic > advertised services > name keywords.
    """
    if appearance is not None:
        label = appearance_name(appearance)
        if not label.startswith("Unknown"):
            return label
    for su in service_uuids or ():
        short = _uuid16(su)
        if short in SERVICE_HINTS:
            return SERVICE_HINTS[short]
    lname = (name or "").lower()
    for keyword, label in NAME_HINTS:
        if keyword in lname:
            return label
    if appearance is not None:
        return appearance_name(appearance)
    return "Unknown"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_scan(args):
    print("Scanning for BLE devices (10s)...")
    found = await BleakScanner.discover(timeout=10.0, return_adv=True)
    items = found.values() if isinstance(found, dict) else found
    items = list(items)
    if not items:
        print("No devices found.")
        return
    print(f"{'Address':<20} {'Type':<20} {'Name'}")
    print("-" * 62)
    rows = []
    for dev, adv in items:
        dtype = classify_device(dev.name, getattr(adv, "service_uuids", None))
        rows.append((dev.address, dtype, dev.name or "(unknown)"))
    for address, dtype, name in sorted(rows, key=lambda r: r[2].lower()):
        print(f"{address:<20} {dtype:<20} {name}")


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


async def _read_device_info(client):
    """Read device name + appearance; return a type label."""
    name, appearance = None, None
    try:
        raw = await client.read_gatt_char(DEVICE_NAME_UUID)
        name = bytes(raw).decode("utf-8", "ignore").strip() or None
    except Exception:
        pass
    try:
        raw = await client.read_gatt_char(APPEARANCE_UUID)
        appearance = int.from_bytes(bytes(raw)[:2], "little")
    except Exception:
        pass
    return classify_device(name=name, appearance=appearance)


async def cmd_monitor(args):
    address = args.address
    interval = max(5, args.interval)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.out or f"battery_{address.replace(':', '')}_{stamp}.csv"

    samples = []
    csv_file = open(out_path, "w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow(["timestamp_utc", "device_type", "battery_pct"])

    print(f"Connecting to {address} ...  (Ctrl+C to stop)")
    device_type = "Unknown"
    try:
        async with BleakClient(address, timeout=20.0) as client:
            if not client.is_connected:
                print("Could not connect. Make sure the device is on, nearby and paired.")
                return
            device_type = await _read_device_info(client)
            print(f"Device type: {device_type}")
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
                writer.writerow([datetime.fromtimestamp(now, timezone.utc).isoformat(),
                                 device_type, level])
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
        print(f"\nDevice: {device_type} ({address})")
        print(f"Session: {len(samples)} samples over {span_h:.2f}h "
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

    s = sub.add_parser("scan", help="Discover nearby BLE devices (with type detection)")
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
