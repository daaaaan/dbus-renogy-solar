# dbus-renogy-solar

Venus OS driver for the **Renogy Rover Boost MPPT** solar charge controller. Reads data from the controller via RS485 Modbus RTU and publishes it as a `com.victronenergy.solarcharger` service on dbus, making it visible in the Victron GX interface and VRM portal.

Integrates with the Venus OS **serial-starter** service — the adapter is detected automatically when plugged in, and the driver starts without manual intervention.

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
- Integrates with Venus OS serial-starter: driver starts automatically when adapter is plugged in

---

## Hardware Requirements

- Victron Cerbo GX (or any Venus OS device with a USB port)
- USB-to-RS485 adapter — **FTDI FT232R chip recommended** (CH341-based adapters are unstable on Venus OS)
- Ethernet patch cable (Cat 5e or better) for the RJ45 RS485 connection

---

## RS485 Wiring

The Renogy Rover Boost has an RJ45 RS485 port. Wire it to your USB-RS485 adapter as follows.

**Renogy RJ45 pinout** (T-568B cable, pin 1 = Brown at the cable end):

> **Note:** The Renogy manual shows the pinout from the socket (receptacle) view, which is mirrored compared to the cable end. The table below shows the correct pins as numbered from the **cable end** with the clip facing away.

| Cable pin | Signal | Wire colour (T-568B) | Connect to |
|-----------|--------|----------------------|------------|
| 5 | GND | Blue/White | GND on adapter |
| 6 | RS485-B (TX/RX−) | Green | B− on adapter |
| 7 | RS485-A (TX/RX+) | Brown/White | A+ on adapter |
| 8 | +5V | Brown | ⚠️ Do NOT connect |
| 1–4 | CAN bus | — | Not used |

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
- Writes `/data/conf/serial-starter.d/renogy.conf` — tells serial-starter to use this driver for Renogy adapters
- Creates the service template in `/data/dbus-renogy-solar/service/` (survives firmware updates)
- Symlinks the template into `/opt/victronenergy/service-templates/`
- Updates `/data/rc.local` to restore the symlink after firmware updates

### 3. Create a udev rule to register the adapter with serial-starter

This rule tells Venus OS's serial-starter that your FTDI adapter is a Renogy MPPT device. Serial-starter will then start the driver automatically whenever the adapter is plugged in.

```bash
# Find your FTDI serial number
dmesg | grep SerialNumber | tail -5
# Example output: usb 5-1: SerialNumber: AQ02EVVK

# Create the udev rule (replace AQ02EVVK with your serial number)
mkdir -p /data/etc/udev/rules.d
cat > /data/etc/udev/rules.d/zz-renogy.rules << 'EOF'
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{serial}=="AQ02EVVK", ENV{VE_SERVICE}="renogy_mppt"
EOF

# Apply immediately
cp /data/etc/udev/rules.d/zz-renogy.rules /etc/udev/rules.d/
udevadm control --reload-rules

# Make persistent across firmware updates
echo 'cp /data/etc/udev/rules.d/zz-renogy.rules /etc/udev/rules.d/ && udevadm control --reload-rules' >> /data/rc.local
```

### 4. Replug the USB adapter

Serial-starter detects devices via udev events. After the rule is in place:

```bash
# Physically replug the USB adapter, then check:
tail -f /data/log/serial-starter/current | tai64nlocal
```

You should see serial-starter identify the device and start `dbus-renogy-solar`.

---

## Verifying It Works

The service name includes the port, e.g. `dbus-renogy-solar.ttyUSB0`:

```bash
svstat /service/dbus-renogy-solar.ttyUSB0
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

Monitor serial-starter activity:
```bash
tail -f /data/log/serial-starter/current | tai64nlocal
```

---

## Configuration

The driver accepts command-line arguments (set in `/data/dbus-renogy-solar/service/run`):

| Argument | Default | Description |
|---|---|---|
| `--port` | (set by serial-starter) | Serial port |
| `--baud` | `9600` | Baud rate |
| `--address` | `1` | Modbus slave address |
| `--instance` | `290` | VRM device instance number |
| `--debug` | off | Enable verbose logging |

Edit `/data/dbus-renogy-solar/service/run` to change arguments. Replug the adapter or reboot for changes to take effect.

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

### Service not starting after replug

Check serial-starter's log for errors:
```bash
tail -20 /data/log/serial-starter/current | tai64nlocal
```

Confirm the udev rule is setting `VE_SERVICE` correctly:
```bash
udevadm info --query=property --name=/dev/ttyUSB0 | grep VE_SERVICE
# Should show: VE_SERVICE=renogy_mppt
```

Check the service-templates symlink exists:
```bash
ls -la /opt/victronenergy/service-templates/dbus-renogy-solar
```

If the symlink is missing (e.g. after a firmware update before reboot), recreate it:
```bash
ln -sf /data/dbus-renogy-solar/service /opt/victronenergy/service-templates/dbus-renogy-solar
```

### "Short response" or "CRC mismatch" errors on startup

These are normal for the first 1–2 polls while the serial buffer settles. If they persist, check wiring.

### FTDI adapter keeps disconnecting

The CH341 USB-serial chip is unstable on Venus OS under load. Switch to an FTDI FT232R-based adapter:
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

### serial-starter integration

Venus OS's `serial-starter` monitors USB serial devices. When an adapter is plugged in:

1. udev fires an `add` event
2. The udev rule sets `ENV{VE_SERVICE}="renogy_mppt"` on the device
3. serial-starter reads this, looks up `renogy_mppt` in `/data/conf/serial-starter.d/renogy.conf`
4. Finds the `dbus-renogy-solar` service template in `/opt/victronenergy/service-templates/`
5. Creates a live service at `/service/dbus-renogy-solar.ttyUSBx/` with `TTY` replaced by the device name
6. daemontools supervises the service

### Driver architecture

The driver runs two concurrent execution contexts:

1. **Serial I/O thread** — opens the RS485 port, polls the Renogy controller every 2 seconds using Modbus RTU, and caches the result in a thread-safe dictionary. Running serial I/O in a dedicated thread is essential: Venus OS's GLib/dbus event loop sets `O_NONBLOCK` on process file descriptors, which causes `pyserial.read()` to return 0 bytes immediately instead of blocking for the controller's response.

2. **GLib main loop** — runs the dbus service and updates all published values from the cache every 2 seconds. This is entirely non-blocking.

### Persistence across firmware updates

| Component | Location | Survives firmware update? |
|---|---|---|
| Driver code | `/data/dbus-renogy-solar/` | Yes |
| Service template | `/data/dbus-renogy-solar/service/` | Yes |
| serial-starter config | `/data/conf/serial-starter.d/renogy.conf` | Yes |
| udev rule source | `/data/etc/udev/rules.d/zz-renogy.rules` | Yes |
| udev rule live copy | `/etc/udev/rules.d/zz-renogy.rules` | No — restored by rc.local |
| service-templates symlink | `/opt/victronenergy/service-templates/dbus-renogy-solar` | No — restored by rc.local |

---

## Uninstalling

```bash
rm -f /opt/victronenergy/service-templates/dbus-renogy-solar
rm -f /data/conf/serial-starter.d/renogy.conf
rm -rf /var/log/dbus-renogy-solar
rm -f /etc/udev/rules.d/zz-renogy.rules /data/etc/udev/rules.d/zz-renogy.rules
udevadm control --reload-rules
```

Remove the lines added to `/data/rc.local` manually.
