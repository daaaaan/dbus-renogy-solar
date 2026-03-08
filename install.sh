#!/bin/bash
# Install script for dbus-renogy-solar on Venus OS (Cerbo GX)
#
# Usage: scp this entire directory to your Cerbo, then run:
#   bash /data/dbus-renogy-solar/install.sh
#
# Prerequisites:
#   - SSH access to Cerbo GX (enable in Settings → General → Access Level → Superuser)
#   - USB-to-RS485 adapter plugged into Cerbo USB port

set -e

INSTALL_DIR="/data/dbus-renogy-solar"
SERVICE_TEMPLATE="$INSTALL_DIR/service"
VICTRON_TEMPLATES="/opt/victronenergy/service-templates"
SERIAL_STARTER_CONF="/data/conf/serial-starter.d/renogy.conf"

echo "=== dbus-renogy-solar installer ==="

# Check we're running on Venus OS
if [ ! -d "/opt/victronenergy" ]; then
    echo "ERROR: This doesn't appear to be a Venus OS system."
    echo "This driver is designed for Victron Cerbo GX / Venus OS."
    exit 1
fi

# Check script is in the right place
if [ ! -f "$INSTALL_DIR/dbus-renogy-solar.py" ]; then
    echo "ERROR: Please copy this directory to $INSTALL_DIR first."
    echo "  scp -r dbus-renogy-solar root@<cerbo-ip>:/data/"
    exit 1
fi

# Install pyserial if missing
if ! python3 -c "import serial" 2>/dev/null; then
    echo "Installing pyserial..."
    pip3 install pyserial
fi

# Create velib_python symlink if not present
VELIB="/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
if [ ! -d "$INSTALL_DIR/ext/velib_python" ]; then
    echo "Linking velib_python..."
    mkdir -p "$INSTALL_DIR/ext"
    ln -sf "$VELIB" "$INSTALL_DIR/ext/velib_python"
fi

chmod +x "$INSTALL_DIR/dbus-renogy-solar.py"

# --- Serial-starter configuration ---
# /data/conf/serial-starter.d/ is automatically included by Venus OS serial-starter
# and survives firmware updates.
echo "Configuring serial-starter..."
mkdir -p "$(dirname "$SERIAL_STARTER_CONF")"
cat > "$SERIAL_STARTER_CONF" << 'EOF'
service renogy_mppt dbus-renogy-solar
EOF

# --- Service template in /data/ (survives firmware updates) ---
# serial-starter replaces TTY with the actual tty device name (e.g. ttyUSB0)
# and starts the service as /service/dbus-renogy-solar.ttyUSB0
echo "Creating service template in $SERVICE_TEMPLATE..."
mkdir -p "$SERVICE_TEMPLATE/log"

cat > "$SERVICE_TEMPLATE/run" << 'EOF'
#!/bin/bash
exec 2>&1
exec python3 /data/dbus-renogy-solar/dbus-renogy-solar.py --port /dev/TTY
EOF
chmod +x "$SERVICE_TEMPLATE/run"

cat > "$SERVICE_TEMPLATE/log/run" << 'EOF'
#!/bin/bash
exec multilog t s25000 n4 /var/log/dbus-renogy-solar
EOF
chmod +x "$SERVICE_TEMPLATE/log/run"

# Symlink into /opt/victronenergy/service-templates/ so Venus OS auto-starts it
echo "Symlinking into $VICTRON_TEMPLATES/..."
ln -sf "$SERVICE_TEMPLATE" "$VICTRON_TEMPLATES/dbus-renogy-solar"

# Create log directory
mkdir -p /var/log/dbus-renogy-solar

# --- rc.local: restore symlink after firmware update ---
RC_LOCAL="/data/rc.local"
touch "$RC_LOCAL"
chmod +x "$RC_LOCAL"

if ! grep -q "dbus-renogy-solar" "$RC_LOCAL" 2>/dev/null; then
    echo "Adding boot restore to $RC_LOCAL..."
    cat >> "$RC_LOCAL" << 'RCEOF'

# dbus-renogy-solar: recreate service-templates symlink after firmware update
[ -L /opt/victronenergy/service-templates/dbus-renogy-solar ] || \
    ln -sf /data/dbus-renogy-solar/service /opt/victronenergy/service-templates/dbus-renogy-solar

# dbus-renogy-solar: restore volatile log directory
mkdir -p /var/log/dbus-renogy-solar
RCEOF
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "NEXT: Create a udev rule so serial-starter recognises your FTDI RS485 adapter."
echo ""
echo "1. Find your adapter's serial number:"
echo "   dmesg | grep SerialNumber | tail -5"
echo "   # e.g. usb 5-1: SerialNumber: AQ02EVVK"
echo ""
echo "2. Create the udev rule (replace AQ02EVVK with your serial number):"
echo "   mkdir -p /data/etc/udev/rules.d"
echo "   cat > /data/etc/udev/rules.d/zz-renogy.rules << 'EOF'"
echo "   ACTION==\"add\", SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0403\", ATTRS{serial}==\"AQ02EVVK\", ENV{VE_SERVICE}=\"renogy_mppt\""
echo "   EOF"
echo ""
echo "3. Apply and persist the rule:"
echo "   cp /data/etc/udev/rules.d/zz-renogy.rules /etc/udev/rules.d/"
echo "   udevadm control --reload-rules"
echo "   echo 'cp /data/etc/udev/rules.d/zz-renogy.rules /etc/udev/rules.d/ && udevadm control --reload-rules' >> /data/rc.local"
echo ""
echo "4. Replug the USB adapter (or reboot) to let serial-starter detect it:"
echo "   # serial-starter will start /service/dbus-renogy-solar.ttyUSBx automatically"
echo ""
echo "Monitor logs:"
echo "  tail -f /var/log/dbus-renogy-solar/current | tai64nlocal"
echo "  tail -f /data/log/serial-starter/current | tai64nlocal"
echo ""
echo "To uninstall:"
echo "  rm -f /opt/victronenergy/service-templates/dbus-renogy-solar $SERIAL_STARTER_CONF"
echo "  rm -rf /var/log/dbus-renogy-solar"
