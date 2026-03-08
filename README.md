# dbus-renogy-solar

Venus OS driver for the **Renogy Rover Boost MPPT** solar charge controller. Reads data from the controller via RS485 Modbus RTU and publishes it as a `com.victronenergy.solarcharger` service on dbus, making it visible in the Victron GX interface and VRM portal.

**Tested with:** Renogy Rover Boost 10A 36V/48V (RCC10RVRB) on Cerbo GX running Venus OS.

---

## Features

- Live solar data: PV voltage, current, and power
- Battery voltage, current, and temperature
- State of charge (SOC)
- Charging state (Bulk / Float / Equalize / Off)
- Daily history: yield (Wh), max power, min/max battery voltage
- Cumulative totals: charge Ah and kWh
- Error code reporting
- Appears natively in the Victron GX display and VRM portal as a solar charger
- Automatic reconnect on serial errors

---

## Hardware Requirements

- Victron Cerbo GX (or any Venus OS device with a USB port)
- USB-to-RS485 adapter — **FTDI FT232R chip recommended** (CH341-based adapters are unstable on Venus OS)
- Ethernet patch cable (Cat 5e or better) for the RJ45 RS485 connection

---

## RS485 Wiring

The Renogy Rover Boost has an RJ45 RS485 port. Wire it to your USB-RS485 adapter as follows.

**Renogy RJ45 pinout** (T-568B standard, pins numbered from left with clip facing away):

| Pin | Signal | Wire colour (T-568B) | Connect to |
|-----|--------|----------------------|------------|
| 1 | +5V | Orange/White | ⚠️ Do NOT connect |
| 2 | RS485-A (TX/RX+) | Orange | A+ on adapter |
| 3 | RS485-B (TX/RX−) | White/Green | B− on adapter |
| 4 | GND | Blue | GND on adapter |
| 5–8 | CAN bus | — | Not used |

> **Important:** The Renogy manual diagram shows the RJ45 socket in receptacle view (mirrored). If you wire from the cable end, pins 2/3/4 as described above are correct.

**RS485 adapter wiring example** (4-pin screw terminal adapter with White/Green/Red/Black wires):

| Adapter wire | Connect to |
|---|---|
| White (A+) | RJ45 pin 2 (Orange) |
| Green (B−) | RJ45 pin 3 (White/Green) |
| Black (GND) | RJ45 pin 4 (Blue) |
| Red (VCC/5V) | Not connected |

---

## Installation

### 1. Copy files to the Cerbo GX

```bash
scp -r dbus-renogy-solar root@<cerbo-ip>:/data/
```

### 2. SSH into the Cerbo and run the installer

```bash
ssh root@<cerbo-ip>
bash /data/dbus-renogy-solar/install.sh
```

The installer:
- Checks you're on Venus OS
- Installs `pyserial` if missing
- Creates a symlink to `velib_python`
- Creates a daemontools service in `/service/dbus-renogy-solar`
- Creates a log directory at `/var/log/dbus-renogy-solar`

### 3. Create a udev rule (critical — prevents Venus OS from hijacking the port)

Venus OS's `serial-starter` service automatically probes every `ttyUSB*` device with multiple protocols (VE.Direct, Modbus, GPS, etc.), which confuses the Renogy controller and prevents reliable communication. Fix this by giving your FTDI adapter a persistent name that serial-starter ignores:

```bash
# Find your FTDI serial number
dmesg | grep -i "ftdi\|SerialNumber" | tail -5
# Example output: usb 5-1: SerialNumber: AQ02EVVK

# Create the udev rule (replace AQ02EVVK with your serial number)
mkdir -p /data/etc/udev/rules.d
cat > /data/etc/udev/rules.d/99-renogy.rules << 'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{serial}=="AQ02EVVK", SYMLINK+="ttyRenogy", MODE="0666"
EOF

# Apply immediately
cp /data/etc/udev/rules.d/99-renogy.rules /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty

# Make persistent across reboots
echo 'cp /data/etc/udev/rules.d/99-renogy.rules /etc/udev/rules.d/ && udevadm control --reload-rules' >> /data/rc.local

# Confirm symlink created
ls -la /dev/ttyRenogy
```

### 4. Update the service run script to use the fixed port

```bash
cat > /service/dbus-renogy-solar/run << 'EOF'
#!/bin/bash
exec 2>&1
PORT=/dev/ttyRenogy
if [ ! -e "$PORT" ]; then
    echo "Renogy adapter not found at $PORT, waiting..."
    sleep 10
    exit 1
fi
exec python3 /data/dbus-renogy-solar/dbus-renogy-solar.py --port "$PORT"
EOF
chmod +x /service/dbus-renogy-solar/run
svc -t /service/dbus-renogy-solar
```

---

## Verifying It Works

Check service status:
```bash
svstat /service/dbus-renogy-solar
```

Watch live logs:
```bash
tail -f /var/log/dbus-renogy-solar/current | tai64nlocal
```

You should see lines like:
```
INFO: Poll OK: SOC=86% PV=45.2V/2.10A Batt=49.9V
```

Inspect dbus values directly:
```bash
dbus -y com.victronenergy.solarcharger.renogy_290 / GetValue
```

---

## Configuration

The driver accepts command-line arguments:

| Argument | Default | Description |
|---|---|---|
| `--port` | `/dev/ttyUSB0` | Serial port |
| `--baud` | `9600` | Baud rate |
| `--address` | `1` | Modbus slave address |
| `--instance` | `290` | VRM device instance number |
| `--debug` | off | Enable verbose logging |

Edit `/service/dbus-renogy-solar/run` to change arguments.

---

## Modbus Register Map

The driver reads the following Renogy holding registers (function code 0x03):

| Address | Register | Scaling |
|---------|----------|---------|
| 0x0100 | Battery SOC (%) | raw |
| 0x0101 | Battery voltage | ÷10 = V |
| 0x0102 | Charging current | ÷100 = A |
| 0x0103 | Temperature (ctrl high byte, batt low byte) | − 100 = °C |
| 0x0104 | Load voltage | ÷10 = V |
| 0x0105 | Load current | ÷100 = A |
| 0x0106 | Load power | W |
| 0x0107 | PV voltage | ÷10 = V |
| 0x0108 | PV current | ÷100 = A |
| 0x0109 | PV power | W |
| 0x010B–0x0115 | Daily statistics | various |
| 0x0118–0x011B | Cumulative totals | various |
| 0x0120 | Charging state | see below |
| 0x0121–0x0122 | Error code | bitmask |

**Charging state mapping:**

| Renogy | Victron | Description |
|--------|---------|-------------|
| 0 | Off | Deactivated |
| 2 | Bulk | MPPT charging |
| 3 | Equalize | Equalizing |
| 4 | Bulk | Boost |
| 5 | Float | Float |

---

## Troubleshooting

### Service not starting
```bash
svstat /service/dbus-renogy-solar
tail -20 /var/log/dbus-renogy-solar/current | tai64nlocal
```

### `/dev/ttyRenogy` not appearing
The udev rule might not have been applied. Check the FTDI serial number matches:
```bash
dmesg | grep SerialNumber
```

### "Short response" or "CRC mismatch" errors on startup
These are normal for the first 1–2 polls while the serial buffer settles. If they persist, check wiring and confirm serial-starter is not interfering:
```bash
fuser /dev/ttyRenogy   # should be empty (just our service) or show our PID
svstat /service/dbus-modbus-client.serial.ttyUSB0 2>/dev/null
```

### FTDI adapter keeps disconnecting
The CH341 USB-serial chip is unstable on Venus OS under load. Switch to an FTDI FT232R-based adapter. You can confirm the chip in use:
```bash
dmesg | grep -i "ch341\|ftdi" | tail -5
```

### Checking FTDI latency (performance tuning)
The FTDI default 16ms latency timer can cause buffering issues. Set it to 1ms:
```bash
echo 1 > /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
```

This does not persist across reboots; add it to `/data/rc.local` if needed.

---

## How It Works

The driver runs two concurrent execution contexts:

1. **Serial I/O thread** — opens the RS485 port, polls the Renogy controller every 2 seconds using Modbus RTU, and caches the result in a thread-safe dictionary. Running serial I/O in a dedicated thread is essential: Venus OS's GLib/dbus event loop sets `O_NONBLOCK` on process file descriptors, which causes `pyserial.read()` to return 0 bytes immediately instead of blocking for the controller's response.

2. **GLib main loop** — runs the dbus service and updates all published values from the cache every 2 seconds. This is entirely non-blocking.

---

## Uninstalling

```bash
rm -rf /service/dbus-renogy-solar /var/log/dbus-renogy-solar
rm -f /etc/udev/rules.d/99-renogy.rules /data/etc/udev/rules.d/99-renogy.rules
udevadm control --reload-rules
```

Remove the line added to `/data/rc.local` manually if present.
