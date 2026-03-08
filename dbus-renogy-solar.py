#!/usr/bin/env python3
"""
Venus OS dbus driver for Renogy Rover Boost MPPT solar charge controller.

Reads data from the Renogy Rover Boost (RCC10RVRB) via RS485 Modbus RTU
and publishes it as a com.victronenergy.solarcharger service on dbus.

Tested with: Renogy Rover Boost 10A 36V/48V (RCC10RVRB)
Connection: USB-to-RS485 adapter → Rover Boost RS485 port (RJ45)

RS485 RJ45 pinout (T-568B cable, pins numbered from cable end):
  Pin 8: +5V (do NOT connect to RS485 adapter)
  Pin 7: RS485-A (TX/RX+)  — Brown/White wire (T-568B)
  Pin 6: RS485-B (TX/RX-)  — Green wire (T-568B)
  Pin 5: GND               — Blue/White wire (T-568B)
  Pin 1-4: CAN bus (not used for RS485)
"""

import fcntl
import logging
import os
import sys
import time
import threading
import serial
import struct

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext", "velib_python"))
from vedbus import VeDbusService
from settingsdevice import SettingsDevice

from gi.repository import GLib

VERSION = "1.0.0"
PRODUCT_NAME = "Renogy Rover Boost MPPT"

# Modbus defaults
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 9600
DEFAULT_SLAVE_ADDR = 1
POLL_INTERVAL_MS = 2000

# Renogy Modbus register addresses (function code 0x03 - Read Holding Registers)
REG_SOC = 0x0100              # Battery state of charge (%)
REG_BATT_VOLTAGE = 0x0101     # Battery voltage (÷10 = V)
REG_BATT_CURRENT = 0x0102     # Battery charging current (÷100 = A)
REG_TEMPERATURE = 0x0103      # Controller & battery temp (high byte=ctrl, low byte=batt, offset by 100)
REG_LOAD_VOLTAGE = 0x0104     # Load voltage (÷10 = V)
REG_LOAD_CURRENT = 0x0105     # Load current (÷100 = A)
REG_LOAD_POWER = 0x0106       # Load power (W)
REG_PV_VOLTAGE = 0x0107       # PV input voltage (÷10 = V)
REG_PV_CURRENT = 0x0108       # PV input current (÷100 = A)
REG_PV_POWER = 0x0109         # PV input power (W)
REG_DAILY_BATT_V_MIN = 0x010B # Min battery voltage today (÷10 = V)
REG_DAILY_BATT_V_MAX = 0x010C # Max battery voltage today (÷10 = V)
REG_DAILY_CHARGE_A_MAX = 0x010D   # Max charging current today (÷100 = A)
REG_DAILY_DISCHARGE_A_MAX = 0x010E # Max discharging current today (÷100 = A)
REG_DAILY_CHARGE_W_MAX = 0x010F   # Max charging power today (W)
REG_DAILY_DISCHARGE_W_MAX = 0x0110 # Max discharging power today (W)
REG_DAILY_CHARGE_AH = 0x0111  # Charging amp-hours today (Ah)
REG_DAILY_DISCHARGE_AH = 0x0112  # Discharging amp-hours today (Ah)
REG_DAILY_CHARGE_WH = 0x0113  # Power generation today (Wh)
REG_DAILY_DISCHARGE_WH = 0x0114  # Power consumption today (Wh)
REG_DAYS_OPERATING = 0x0115   # Total operating days
REG_TOTAL_CHARGE_AH_H = 0x0118   # Total charging Ah (high word)
REG_TOTAL_CHARGE_AH_L = 0x0119   # Total charging Ah (low word)
REG_TOTAL_CHARGE_KWH_H = 0x011A  # Total charging kWh (high word)
REG_TOTAL_CHARGE_KWH_L = 0x011B  # Total charging kWh (low word)
REG_CHARGING_STATE = 0x0120   # Charging state (low byte)
REG_ERROR_CODE_H = 0x0121     # Error code (high word)
REG_ERROR_CODE_L = 0x0122     # Error code (low word)

# Renogy charging states → Victron states
# Victron: 0=Off, 2=Fault, 3=Bulk, 4=Absorption, 5=Float, 6=Storage, 7=Equalize
RENOGY_STATE_MAP = {
    0: 0,   # Deactivated → Off
    1: 0,   # Activated → Off (no charging)
    2: 3,   # MPPT charging → Bulk
    3: 7,   # Equalizing → Equalize
    4: 3,   # Boost → Bulk
    5: 5,   # Float → Float
    6: 0,   # Current limiting → Off
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dbus-renogy-solar")


class RenogyModbus:
    """Minimal Modbus RTU client for Renogy charge controllers."""

    def __init__(self, port, baudrate=9600, slave_addr=1, timeout=3.0):
        self.slave_addr = slave_addr
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self.serial = None
        self._open()

    def _open(self):
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
            self.serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
            )
        except serial.SerialException as e:
            log.warning(f"Could not open {self._port}: {e}")
            self.serial = None

    def reconnect(self):
        log.info(f"Reconnecting to {self._port}...")
        self._open()

    def close(self):
        if self.serial and self.serial.is_open:
            self.serial.close()

    @staticmethod
    def _crc16(data):
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def read_registers(self, start_addr, count):
        """Read `count` holding registers starting at `start_addr`. Returns list of 16-bit values."""
        if not self.serial or not self.serial.is_open:
            raise IOError("Serial port not open")

        # GLib/dbus event loop sets O_NONBLOCK on process FDs; force blocking mode.
        fd = self.serial.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if flags & os.O_NONBLOCK:
            log.debug(f"Clearing O_NONBLOCK on serial FD (flags={flags:#x})")
            fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

        request = struct.pack(">BBH H", self.slave_addr, 0x03, start_addr, count)
        crc = self._crc16(request)
        request += struct.pack("<H", crc)

        self.serial.reset_input_buffer()
        self.serial.write(request)
        time.sleep(0.1)  # inter-frame gap for Modbus RTU

        # Response: addr(1) + func(1) + byte_count(1) + data(2*count) + crc(2)
        expected_len = 3 + 2 * count + 2
        response = self.serial.read(expected_len)

        if len(response) < expected_len:
            raise IOError(f"Short response: got {len(response)} bytes, expected {expected_len}")

        # Verify CRC
        recv_crc = struct.unpack("<H", response[-2:])[0]
        calc_crc = self._crc16(response[:-2])
        if recv_crc != calc_crc:
            raise IOError(f"CRC mismatch: received 0x{recv_crc:04X}, calculated 0x{calc_crc:04X}")

        # Check for Modbus error response
        if response[1] & 0x80:
            raise IOError(f"Modbus error: function 0x{response[1]:02X}, code {response[2]}")

        # Parse register values
        values = []
        for i in range(count):
            offset = 3 + i * 2
            values.append(struct.unpack(">H", response[offset : offset + 2])[0])
        return values

    def read_register(self, addr):
        """Read a single holding register."""
        return self.read_registers(addr, 1)[0]


class RenogySolarData:
    """Reads and caches data from the Renogy Rover Boost in a background thread.

    Serial I/O runs in a dedicated thread so GLib's event loop cannot interfere
    with blocking reads (GLib/dbus sets O_NONBLOCK on process FDs).
    """

    POLL_INTERVAL_S = 2.0

    def __init__(self, port, baudrate, slave_addr):
        self.modbus = RenogyModbus(port, baudrate, slave_addr)
        self._data = {}
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="renogy-poll")
        self._thread.start()

    @property
    def data(self):
        with self._lock:
            return dict(self._data)

    @property
    def connected(self):
        with self._lock:
            return self._connected

    def close(self):
        self._stop.set()
        self.modbus.close()

    def _poll_loop(self):
        while not self._stop.is_set():
            self._do_poll()
            self._stop.wait(self.POLL_INTERVAL_S)

    def _do_poll(self):
        try:
            # Read main block: 0x0100 - 0x0109 (10 registers)
            regs = self.modbus.read_registers(REG_SOC, 10)
            new_data = {}
            new_data["soc"] = regs[0]
            new_data["batt_v"] = regs[1] / 10.0
            new_data["batt_a"] = regs[2] / 100.0
            # Temperature: high byte = controller, low byte = battery
            # Each offset by 100 to handle negatives (value - 100 = actual temp)
            raw_temp = regs[3]
            ctrl_temp_sign = -1 if (raw_temp >> 15) & 1 else 1
            batt_temp_sign = -1 if (raw_temp >> 7) & 1 else 1
            new_data["ctrl_temp"] = ctrl_temp_sign * ((raw_temp >> 8) & 0x7F)
            new_data["batt_temp"] = batt_temp_sign * (raw_temp & 0x7F)
            new_data["load_v"] = regs[4] / 10.0
            new_data["load_a"] = regs[5] / 100.0
            new_data["load_w"] = regs[6]
            new_data["pv_v"] = regs[7] / 10.0
            new_data["pv_a"] = regs[8] / 100.0
            new_data["pv_w"] = regs[9]

            time.sleep(0.15)  # give controller time to recover between requests

            # Read daily stats: 0x010B - 0x0115 (11 registers)
            regs2 = self.modbus.read_registers(REG_DAILY_BATT_V_MIN, 11)
            new_data["daily_batt_v_min"] = regs2[0] / 10.0
            new_data["daily_batt_v_max"] = regs2[1] / 10.0
            new_data["daily_charge_a_max"] = regs2[2] / 100.0
            new_data["daily_discharge_a_max"] = regs2[3] / 100.0
            new_data["daily_charge_w_max"] = regs2[4]
            new_data["daily_discharge_w_max"] = regs2[5]
            new_data["daily_charge_ah"] = regs2[6]
            new_data["daily_discharge_ah"] = regs2[7]
            new_data["daily_charge_wh"] = regs2[8]
            new_data["daily_discharge_wh"] = regs2[9]
            new_data["days_operating"] = regs2[10]

            time.sleep(0.15)

            # Read totals: 0x0118 - 0x011B (4 registers)
            regs3 = self.modbus.read_registers(REG_TOTAL_CHARGE_AH_H, 4)
            new_data["total_charge_ah"] = (regs3[0] << 16) | regs3[1]
            new_data["total_charge_kwh"] = (regs3[2] << 16) | regs3[3]

            time.sleep(0.15)

            # Read charging state + error codes: 0x0120 - 0x0122 (3 registers, consolidated)
            regs4 = self.modbus.read_registers(REG_CHARGING_STATE, 3)
            new_data["charging_state"] = regs4[0] & 0xFF
            new_data["error_code"] = (regs4[1] << 16) | regs4[2]

            with self._lock:
                self._data = new_data
                self._connected = True
            log.info("Poll OK: SOC=%d%% PV=%.1fV/%.2fA Batt=%.1fV" % (
                new_data["soc"], new_data["pv_v"], new_data["pv_a"], new_data["batt_v"]))

        except serial.SerialException as e:
            log.error(f"Poll failed: {e}")
            with self._lock:
                self._connected = False
            self.modbus.reconnect()
        except Exception as e:
            log.error(f"Poll failed: {e}")
            with self._lock:
                self._connected = False


class DbusRenogySolarService:
    """Publishes Renogy Rover Boost data as a Victron solarcharger on dbus."""

    def __init__(self, port, baudrate, slave_addr, instance=290):
        self.renogy = RenogySolarData(port, baudrate, slave_addr)
        self.instance = instance
        self._connected = False

        servicename = f"com.victronenergy.solarcharger.renogy_{instance}"

        self._dbusservice = VeDbusService(servicename, register=False)

        # Management paths
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", VERSION)
        self._dbusservice.add_path("/Mgmt/Connection", f"RS485 {port}")

        # Mandatory device info
        self._dbusservice.add_path("/DeviceInstance", instance)
        self._dbusservice.add_path("/ProductId", 0)
        self._dbusservice.add_path("/ProductName", PRODUCT_NAME)
        self._dbusservice.add_path("/CustomName", PRODUCT_NAME)
        self._dbusservice.add_path("/FirmwareVersion", VERSION)
        self._dbusservice.add_path("/HardwareVersion", 0)
        self._dbusservice.add_path("/Serial", "")
        self._dbusservice.add_path("/Connected", 0)

        # Solar charger paths
        self._dbusservice.add_path("/Pv/V", None, gettextcallback=lambda p, v: f"{v:.1f}V")
        self._dbusservice.add_path("/Pv/I", None, gettextcallback=lambda p, v: f"{v:.2f}A")
        self._dbusservice.add_path("/Dc/0/Voltage", None, gettextcallback=lambda p, v: f"{v:.2f}V")
        self._dbusservice.add_path("/Dc/0/Current", None, gettextcallback=lambda p, v: f"{v:.2f}A")
        self._dbusservice.add_path("/Dc/0/Temperature", None, gettextcallback=lambda p, v: f"{v:.0f}°C")
        self._dbusservice.add_path("/Yield/Power", None, gettextcallback=lambda p, v: f"{v:.0f}W")
        self._dbusservice.add_path("/Yield/System", None, gettextcallback=lambda p, v: f"{v:.2f}kWh")
        self._dbusservice.add_path("/Yield/User", None, gettextcallback=lambda p, v: f"{v:.3f}kWh")
        self._dbusservice.add_path("/State", 0)
        self._dbusservice.add_path("/Mode", 1)  # 1 = On
        self._dbusservice.add_path("/MppOperationMode", 0)
        self._dbusservice.add_path("/ErrorCode", 0)

        # Extra paths for richer VRM data
        self._dbusservice.add_path("/History/Daily/0/Yield", None, gettextcallback=lambda p, v: f"{v:.3f}kWh")
        self._dbusservice.add_path("/History/Daily/0/MaxPower", None, gettextcallback=lambda p, v: f"{v:.0f}W")
        self._dbusservice.add_path("/History/Daily/0/MaxPvVoltage", None, gettextcallback=lambda p, v: f"{v:.1f}V")
        self._dbusservice.add_path("/History/Daily/0/MaxBatteryVoltage", None, gettextcallback=lambda p, v: f"{v:.1f}V")
        self._dbusservice.add_path("/History/Daily/0/MinBatteryVoltage", None, gettextcallback=lambda p, v: f"{v:.1f}V")

        self._dbusservice.register()
        log.info(f"Registered dbus service: {servicename}")

        GLib.timeout_add(POLL_INTERVAL_MS, self._update)

    def _update(self):
        try:
            if self.renogy.connected:
                d = self.renogy.data
                self._dbusservice["/Connected"] = 1
                self._connected = True

                # PV input
                self._dbusservice["/Pv/V"] = d["pv_v"]
                self._dbusservice["/Pv/I"] = d["pv_a"]

                # DC output (battery side)
                self._dbusservice["/Dc/0/Voltage"] = d["batt_v"]
                self._dbusservice["/Dc/0/Current"] = d["batt_a"]
                self._dbusservice["/Dc/0/Temperature"] = d["ctrl_temp"]

                # Power & yield
                self._dbusservice["/Yield/Power"] = d["pv_w"]
                self._dbusservice["/Yield/System"] = d["total_charge_kwh"]
                self._dbusservice["/Yield/User"] = round(d["daily_charge_wh"] / 1000.0, 3)

                # State mapping
                renogy_state = d.get("charging_state", 0)
                victron_state = RENOGY_STATE_MAP.get(renogy_state, 0)
                self._dbusservice["/State"] = victron_state
                self._dbusservice["/MppOperationMode"] = 2 if renogy_state == 2 else (1 if victron_state > 0 else 0)

                # Error
                error_code = d.get("error_code", 0)
                self._dbusservice["/ErrorCode"] = 0 if error_code == 0 else 1

                # Daily history
                self._dbusservice["/History/Daily/0/Yield"] = round(d["daily_charge_wh"] / 1000.0, 3)
                self._dbusservice["/History/Daily/0/MaxPower"] = d["daily_charge_w_max"]
                self._dbusservice["/History/Daily/0/MaxBatteryVoltage"] = d["daily_batt_v_max"]
                self._dbusservice["/History/Daily/0/MinBatteryVoltage"] = d["daily_batt_v_min"]

            else:
                if self._connected:
                    self._dbusservice["/Connected"] = 0
                    self._connected = False

        except Exception as e:
            log.error(f"Update error: {e}")

        return True  # Keep the timer running


def main():
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Venus OS driver for Renogy Rover Boost MPPT")
    parser.add_argument("-p", "--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("-a", "--address", type=int, default=DEFAULT_SLAVE_ADDR, help=f"Modbus slave address (default: {DEFAULT_SLAVE_ADDR})")
    parser.add_argument("-i", "--instance", type=int, default=290, help="VRM device instance (default: 290)")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info(f"Starting {PRODUCT_NAME} driver v{VERSION}")
    log.info(f"Port: {args.port}, Baud: {args.baud}, Address: {args.address}, Instance: {args.instance}")

    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)

    service = DbusRenogySolarService(
        port=args.port,
        baudrate=args.baud,
        slave_addr=args.address,
        instance=args.instance,
    )

    log.info("Connected to dbus. Starting main loop...")
    mainloop = GLib.MainLoop()

    try:
        mainloop.run()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        service.renogy.close()


if __name__ == "__main__":
    main()
