rm -f /tmp/update_ems.sh
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/raw/refs/heads/dmanner/hems/ext/s2/update_ems.sh
chmod a+x /tmp/update_ems.sh
chmod 755 /tmp/update_ems.sh
/tmp/update_ems.sh
