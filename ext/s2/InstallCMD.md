rm -f /tmp/update_hems.sh
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/raw/refs/heads/dmanner/hems/ext/s2/update_hems.sh
chmod a+x /tmp/update_hems.sh
chmod 755 /tmp/update_hems.sh
/tmp/update_hems.sh
