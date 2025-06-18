#!/bin/sh

# Install-Script for beta-hems. This file should take care to download all required dependencies and get
# HEMS up and running on any gx device. Data is pulled from github, so Internet-Connection is required. 

echo "============================"
echo "HEMS development Updater 1.0"
echo "============================"
echo ""
echo "!!! If you want to use FAKE-BMS for display purpose, please read the docu and configure your system accordingly before proceeding !!!"
echo "    Doku: https://github.com/victronenergy/dbus-systemcalc-py/blob/dmanner/hems/ext/s2/readme.md"
echo ""
echo -n "Enter the number of FAKE-BMS required. (1 is reserved for overhead display, so use consumerCount + 1). 0 to disable FAKE_BMS usage:"
read num

if [[ "$num" =~ ^-?[0-9]+$ ]]; then
    echo "You entered : $num"
else
    echo "Invalid input. Not an integer."
    exit
fi

echo "Proceeding with setup:"

echo " > Deleting eventually outdated temporary files..."
rm -f "/tmp/hems.zip" > /dev/null 2>&1
rm -rf "/tmp/hems" > /dev/null 2>&1

echo " > Downloading latest HEMS from github..."
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/archive/refs/heads/dmanner/hems.zip > /dev/null 2>&1

echo " > Extracting HEMS..."
unzip /tmp/hems.zip -d /tmp/hems > /dev/null 2>&1

echo " > Updating OPKG ..."
opkg update > /dev/null 2>&1

echo " > Installing python3-pip through OPKG..."
yes | opkg install python3-pip > /dev/null 2>&1

echo " > Installing tzdata through PIP..."
python -m pip install tzdata > /dev/null 2>&1

echo " > Installing s2python through PIP (dependency purpose)..."
python -m pip install s2-python > /dev/null 2>&1

echo " > Updating /usr/lib/python3.12/site-packages/zoneinfo ..."
rm -rf /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1
mv /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2/zoneinfo /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1

echo " > Updating /usr/lib/python3.12/site-packages/s2python ..."
rm -rf /usr/lib/python3.12/site-packages/s2python  > /dev/null 2>&1
mv /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2/s2python /usr/lib/python3.12/site-packages/s2python  > /dev/null 2>&1

if [ "$num" -gt 0 ]; then
    echo " > Altering hems.py to enable FAKE-BMS usage with a count of $num"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = True' /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/hems.py
    sed -Ei "s/^(\s+)self\.available_fake_bms\s*=.*/\\1self\.available_fake_bms = range(1, $num)/" /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/hems.py
else
    echo " > Altering hems.py to disable FAKE-BMS usage"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = False' /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/hems.py
fi

echo " > Copying hems.py to /opt/victronenergy/dbus-systemcalc-py/delegates/ ..."
cp -f /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/hems.py /opt/victronenergy/dbus-systemcalc-py/delegates

echo " > Copying s2-folder to /opt/victronenergy/dbus-systemcalc-py/ext/s2 ..."
cp -rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2 /opt/victronenergy/dbus-systemcalc-py/ext

echo " > Copying aiovelib to /opt/victronenergy/dbus-systemcalc-py/ext/aiovelib ..."
cp -rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/aiovelib /opt/victronenergy/dbus-systemcalc-py/ext

if [ "$num" -gt 0 ]; then
    echo " > Making FAKE-BMS service runnable..."
    chmod a+x /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service/run
    chmod 755 /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service/run

    echo " > Symlinking FAKE-BMS service as /service/fake_bms_service ..."
    ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service /service/fake_bms_service

    echo " > Adding FAKE-BMS service to rc.local..."
    line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service /service/fake_bms_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Starting FAKE-BMS service ..."
    svc -u /service/fake_bms_service
else
    echo " > Removing FAKE-BMS service from rc.local..."
    line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service /service/fake_bms_service"
    file="filename.txt"

    sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

    echo " > Stopic FAKE-BMS service if running..."
    svc -d /service/fake_bms_service
    svc -k /service/fake_bms_service

    echo " > Removing FAKE-BMS service if existing..."
    rm "/service/fake_bms_service"
fi

echo " > Making Shelly-Mock service runnable..."
chmod a+x /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi_service/run
chmod 755 /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi_service/run

echo " > Symlinking Shelly-Mock service as /service/shellyMock_OMBC_Multi_service ..."
ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi_service /service/shellyMock_OMBC_Multi_service

echo " > Adding Shelly-Mock service to rc.local..."
line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi_service /service/shellyMock_OMBC_Multi_service"
file="/data/rc.local"
grep -qxF "$line" "$file" || echo "$line" >> "$file"

echo " > Starting Shelly-Mock service ..."
svc -u /service/shellyMock_OMBC_Multi_service

echo ""
echo "Setup done. HEMS is set to a disabled state for now. Steps to proceed:"
