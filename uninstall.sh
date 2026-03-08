#!/bin/bash
# Uninstall dbus-renogy-solar from Venus OS

echo "=== dbus-renogy-solar uninstaller ==="

# Stop all running instances (serial-starter creates dbus-renogy-solar.ttyUSBx)
echo "Stopping services..."
for svc_dir in /service/dbus-renogy-solar.*; do
    [ -e "$svc_dir" ] && svc -d "$svc_dir" 2>/dev/null && svc -x "$svc_dir" 2>/dev/null
done

# Remove serial-starter config (prevents auto-restart on plug)
echo "Removing serial-starter config..."
rm -f /data/conf/serial-starter.d/renogy.conf

# Remove service template symlink
rm -f /opt/victronenergy/service-templates/dbus-renogy-solar

# Remove service directories created by serial-starter
rm -rf /service/dbus-renogy-solar.*

# Remove udev rules
echo "Removing udev rules..."
rm -f /etc/udev/rules.d/zz-renogy.rules /etc/udev/rules.d/99-renogy.rules
rm -f /data/etc/udev/rules.d/zz-renogy.rules /data/etc/udev/rules.d/99-renogy.rules
udevadm control --reload-rules 2>/dev/null

# Remove log directory
rm -rf /var/log/dbus-renogy-solar

# Clean rc.local entries
if [ -f /data/rc.local ]; then
    echo "Cleaning /data/rc.local..."
    sed -i '/dbus-renogy-solar/d' /data/rc.local
fi

echo ""
echo "=== Uninstall complete ==="
echo "Driver files remain in /data/dbus-renogy-solar/ — remove manually if desired:"
echo "  rm -rf /data/dbus-renogy-solar"
