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

if [ $num > 0 ]; then
    echo " > Altering hems.py to enable FAKE-BMS usage with a count of $num"

else
    echo " > Altering hems.py to disable FAKE-BMS usage"
fi

echo ""
echo "Setup done. HEMS is left in an disabled state for now (unless it was already enabled). Steps to proceed:"
