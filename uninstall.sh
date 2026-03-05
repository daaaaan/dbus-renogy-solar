#!/bin/bash
# Uninstall dbus-renogy-solar from Venus OS
set -e

echo "Stopping service..."
svc -d /service/dbus-renogy-solar 2>/dev/null || true

echo "Removing service..."
rm -rf /service/dbus-renogy-solar
rm -rf /var/log/dbus-renogy-solar

echo "Done. Driver files remain in /data/dbus-renogy-solar/ — remove manually if desired."
