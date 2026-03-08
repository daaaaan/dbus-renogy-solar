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
SERVICE_DIR="/service/dbus-renogy-solar"

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

# Create daemontools service directory
echo "Creating service..."
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/run" << 'EOF'
#!/bin/bash
exec 2>&1
# Detect the USB-RS485 adapter port
PORT=$(ls /dev/ttyUSB* 2>/dev/null | head -n 1)
if [ -z "$PORT" ]; then
    echo "No USB serial adapter found, waiting..."
    sleep 10
    exit 1
fi
exec python3 /data/dbus-renogy-solar/dbus-renogy-solar.py --port "$PORT"
EOF
chmod +x "$SERVICE_DIR/run"

# Create log directory
mkdir -p "$SERVICE_DIR/log"
cat > "$SERVICE_DIR/log/run" << 'EOF'
#!/bin/bash
exec multilog t s25000 n4 /var/log/dbus-renogy-solar
EOF
chmod +x "$SERVICE_DIR/log/run"
mkdir -p /var/log/dbus-renogy-solar

echo ""
echo "=== Installation complete ==="
echo ""
echo "The service will start automatically. Check status with:"
echo "  svstat /service/dbus-renogy-solar"
echo ""
echo "View logs:"
echo "  tail -f /var/log/dbus-renogy-solar/current | tai64nlocal"
echo ""
echo "To configure a specific port or address, edit:"
echo "  $SERVICE_DIR/run"
echo ""
echo "To uninstall:"
echo "  rm -rf /service/dbus-renogy-solar /var/log/dbus-renogy-solar"
