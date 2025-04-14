#!/bin/sh

# Install-Script for beta-hems. This file should take care to download all required dependencies and get
# HEMS up and running on any gx device. Data is pulled from github, so Internet-Connection is required. 

mount -o remount,rw /dev/root
echo "==================================================================================================================="
echo "EMS development Updater 1.0"
echo "==================================================================================================================="
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

echo -n "Setup EVCS mock? 0=No, 1=SinglePhase, 3=ThreePhase:"
read num_evcs

if [[ "$num_evcs" =~ ^-?[0-9]+$ ]]; then
    echo "You entered (EVCS): $num_evcs"
else
    echo "Invalid input. Not an integer."
    exit
fi

echo -n "Setup Shelly mock or use regular dbus-shelly? 0=mock, 1=dbus-shelly:"
read num_mock

if [[ "$num_mock" =~ ^-?[0-9]+$ ]]; then
    echo "You entered (Mock or dbus-shelly): $num_mock"
else
    echo "Invalid input. Not an integer."
    exit
fi

echo "Proceeding with setup:"

echo " > Deleting eventually outdated temporary files..."
rm -f "/tmp/hems.zip" > /dev/null 2>&1
rm -rf "/tmp/hems" > /dev/null 2>&1
rm -f "/tmp/dbus-shelly.zip" > /dev/null 2>&1
rm -rf "/tmp/dbus-shelly" > /dev/null 2>&1

echo " > Downloading latest EMS from github..."
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/archive/refs/heads/dmanner/hems.zip > /dev/null 2>&1

echo " > Extracting EMS..."
unzip /tmp/hems.zip -d /tmp/hems > /dev/null 2>&1

echo " > Updating OPKG ..."
opkg update > /dev/null 2>&1

echo " > Installing python3-pip through OPKG..."
yes | opkg install python3-pip > /dev/null 2>&1

echo " > Installing tzdata through PIP..."
python -m pip install tzdata > /dev/null 2>&1

echo " > Installing aiohttp through PIP..."
python -m pip install aiohttp > /dev/null 2>&1

echo " > Installing websockets through PIP..."
python -m pip install websockets > /dev/null 2>&1

echo " > Installing s2python through PIP (dependency purpose)..."
python -m pip install s2-python > /dev/null 2>&1

echo " > Updating /usr/lib/python3.12/site-packages/zoneinfo ..."
rm -Rf /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1
mv /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2/zoneinfo /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1

echo " > Updating /usr/lib/python3.12/site-packages/s2python ..."
rm -Rf /usr/lib/python3.12/site-packages/s2python  > /dev/null 2>&1
mv /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2/s2python /usr/lib/python3.12/site-packages/s2python  > /dev/null 2>&1

if [ "$num" -gt 0 ]; then
    echo " > Altering ems.py to enable FAKE-BMS usage with a count of $num"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = True' /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/ems.py
    sed -Ei "s/^(\s+)self\.available_fake_bms\s*=.*/\\1self\.available_fake_bms = range(1, $num)/" /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/ems.py
else
    echo " > Altering ems.py to disable FAKE-BMS usage"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = False' /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/ems.py
fi

echo " > Copying ems.py to /opt/victronenergy/dbus-systemcalc-py/delegates/ ..."
cp -f /tmp/hems/dbus-systemcalc-py-dmanner-hems/delegates/ems.py /opt/victronenergy/dbus-systemcalc-py/delegates

echo " > Copying s2-folder to /opt/victronenergy/dbus-systemcalc-py/ext/s2 ..."
cp -Rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/s2 /opt/victronenergy/dbus-systemcalc-py/ext

echo " > Copying aiovelib to /opt/victronenergy/dbus-systemcalc-py/ext/aiovelib ..."
cp -Rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/aiovelib /opt/victronenergy/dbus-systemcalc-py/ext

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

    echo " > Altering fake_bms.py to enable FAKE-BMS usage with a count of $num"
    sed -Ei "s/^available_fake_bms\s*=.*/available_fake_bms = range(0, $num)/" /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms.py

    echo " > Starting FAKE-BMS service ..."
    svc -u /service/fake_bms_service
else
    echo " > Removing FAKE-BMS service from rc.local..."
    line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/fake_bms_service /service/fake_bms_service"
    file="/data/rc.local"

    sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

    echo " > Stopic FAKE-BMS service if running..."
    svc -d /service/fake_bms_service
    svc -k /service/fake_bms_service

    echo " > Removing FAKE-BMS service if existing..."
    rm "/service/fake_bms_service"
fi

echo " > Stopic EVCS service if running..."
svc -d /service/mock_OMBC_EVCS_AmpModes_service
svc -k /service/mock_OMBC_EVCS_AmpModes_service

echo " > Stopic Shelly Mock service if running..."
svc -d /service/shellyMock_OMBC_Multi_service
svc -k /service/shellyMock_OMBC_Multi_service

echo " > Removing EVCS Services if installed."
line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service /service/mock_OMBC_EVCS_AmpModes_service"
file="/data/rc.local"
sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

#legacy, remove single phase mock
line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_SinglePhase_service /service/mock_OMBC_EVCS_AmpModes_service"
file="/data/rc.local"
sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

rm "/service/mock_OMBC_EVCS_AmpModes_service"

echo " > Removing ShellyMock Services if installed."
line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi_service /service/shellyMock_OMBC_Multi_service"
file="/data/rc.local"
sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

rm "/service/shellyMock_OMBC_Multi_service"

if [ "$num_evcs" -eq 1 ]; then
    echo " > Installing asyncio_glib through PIP..."
    python -m pip install asyncio_glib > /dev/null 2>&1

    echo " > Setting UP EVCS Service (1 Phase)..."
    chmod a+x /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service/run
    chmod 755 /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service/run

    #set desired phases
    sed -Ei '/PHASE_MODE_CONFIG\s*=\s*(\d+)/c\PHASE_MODE_CONFIG = 1' /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service.py

    echo " > Symlinking EVCS Service as /service/mock_OMBC_EVCS_AmpModes_service ..."
    ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service /service/mock_OMBC_EVCS_AmpModes_service

    echo " > Adding EVCS Service  to rc.local..."
    line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service /service/mock_OMBC_EVCS_AmpModes_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Starting EVCS Service ..."
    svc -u /service/mock_OMBC_EVCS_AmpModes_service
fi

if [ "$num_evcs" -eq 3 ]; then
    echo " > Installing asyncio_glib through PIP..."
    python -m pip install asyncio_glib > /dev/null 2>&1
    
    echo " > Setting UP EVCS Service (3 Phase)..."
    chmod a+x /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service/run
    chmod 755 /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service/run

    #set desired phases
    sed -Ei '/PHASE_MODE_CONFIG\s*=\s*(\d+)/c\PHASE_MODE_CONFIG = 1' /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service.py

    echo " > Symlinking EVCS Service as /service/mock_OMBC_EVCS_AmpModes_service ..."
    ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service /service/mock_OMBC_EVCS_AmpModes_service

    echo " > Adding EVCS Service  to rc.local..."
    line="ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/mock_OMBC_EVCS_AmpModes_service /service/mock_OMBC_EVCS_AmpModes_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Starting EVCS Service ..."
    svc -u /service/mock_OMBC_EVCS_AmpModes_service
fi

if [ "$num_mock" -eq 0 ]; then
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
fi

if [ "$num_mock" -eq 1 ]; then
    echo " > downloading latest shelly-dbus from github..."
    wget -P /tmp https://github.com/victronenergy/dbus-shelly/archive/refs/heads/rein/shelly_s2.zip -O "/tmp/dbus-shelly.zip" > /dev/null 2>&1

    echo " > Extracting shelly-dbus..."
    unzip /tmp/dbus-shelly.zip -d /tmp/dbus-shelly > /dev/null 2>&1
    
    echo " > Copying shelly-dbus..."
    cp -Rf /tmp/dbus-shelly/dbus-shelly-rein-shelly_s2/* /opt/victronenergy/dbus-shelly
    cp -Rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/ext/aiovelib /opt/victronenergy/dbus-shelly/ext

    echo " > Restarting shelly-dbus service..."
    svc -t /service/dbus-shelly
fi

echo " > Updating /opt/victronenergy/dbus-systemcalc-py/delegates/__init__.py if required..."
line="from delegates.ems import EMS"
file="/opt/victronenergy/dbus-systemcalc-py/delegates/__init__.py"
grep -qxF "$line" "$file" || echo "$line" >> "$file"

echo " > Updating /opt/victronenergy/dbus-systemcalc-py/dbus_systemcalc.py if required..."
grep -qF "delegates.EMS()," "/opt/victronenergy/dbus-systemcalc-py/dbus_systemcalc.py" || sed -Ei 's/^[[:space:]]*delegates\.DynamicEss\(\),$/\t\t\tdelegates.DynamicEss(),\n\t\t\tdelegates.EMS(),/' /opt/victronenergy/dbus-systemcalc-py/dbus_systemcalc.py

echo ""
echo "==================================================================================================================="
echo ""
echo "Setup done. EMS is left in a disabled state for now, if first install."
echo ""
