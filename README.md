# Bluetooth Battery Discharge Rate Detector

A small portable tool that polls a Bluetooth LE device's standard **Battery
Service (0x180F)** and computes its **discharge rate (%/hour)** plus an
estimated time-to-empty. Logs every reading to CSV for later analysis.

## Requirements

- Python 3.9+
- Windows 10+ / Linux / macOS with a Bluetooth LE adapter
- `pip install bleak`

## Usage

Discover nearby BLE devices:

```sh
python bt_battery_drain.py scan
```

Monitor a device (Ctrl+C to stop):

```sh
python bt_battery_drain.py monitor AA:BB:CC:DD:EE:FF
```

Options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--interval N` | 60 | Seconds between battery reads (min 5) |
| `--duration N` | 0 | Stop after N seconds (0 = run forever) |
| `--out FILE` | auto | CSV log path |

Example — 4-hour session, reading every 30 seconds:

```sh
python bt_battery_drain.py monitor AA:BB:CC:DD:EE:FF --interval 30 --duration 14400
```

Live output looks like:

```
Time       Level    Rate           ETA empty
------------------------------------------------
14:02:11    87%     collecting...  --
14:12:11    86%     -5.83 %/h      14h 45m
```

## How the rate is computed

A least-squares linear fit over all samples in the session (battery % vs
elapsed hours). The rate only appears after ~2 minutes of data; accuracy
improves with longer sessions.

## Limitations

- The device **must expose the standard BLE Battery Service (0x180F)**.
  Many headsets/phones do; some expose battery only over classic Bluetooth
  profiles, which this tool cannot read.
- Some devices report battery in coarse steps (5–10%). With coarse
  granularity, run longer sessions for a meaningful rate.
- On Windows the device generally needs to be **paired** first.
- This measures the device's *reported* level, not true cell voltage —
  treat the rate as an estimate.

## CSV format

```csv
timestamp_utc,battery_pct
2026-09-29T08:32:11+00:00,87
```

## License

MIT
