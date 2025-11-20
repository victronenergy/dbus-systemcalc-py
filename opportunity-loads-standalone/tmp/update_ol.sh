#!/bin/sh

# Install-Script for beta-hems. This file should take care to download all required dependencies and get
# HEMS up and running on any gx device. Data is pulled from github, so Internet-Connection is required. 
# FIX: change to opkg based install.

mount -o remount,rw /dev/root
echo "==================================================================================================================="
echo "OpportunityLoads development Updater 1.2"
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

echo "Proceeding with setup:"

echo " > Deleting eventually outdated temporary files..."
rm -f "/tmp/hems.zip" > /dev/null 2>&1
rm -rf "/tmp/hems" > /dev/null 2>&1

echo " > Downloading latest OpportunityLoads from github..."
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/archive/refs/heads/dmanner/hems.zip > /dev/null 2>&1

echo " > Extracting OpportunityLoads..."
unzip /tmp/hems.zip -d /tmp/hems > /dev/null 2>&1

echo " > Switching OPKG Feed ..."
/opt/victronenergy/swupdate-scripts/set-feed.sh testing && opkg update

echo " > Installing python3-pip through OPKG..."
yes | opkg install python3-pip > /dev/null 2>&1

echo " > Installing tzdata through PIP..."
python -m pip install tzdata > /dev/null 2>&1

echo " > Installing s2-python through OPKG..."
yes | opkg install s2-python > /dev/null 2>&1

echo " > Updating /usr/lib/python3.12/site-packages/zoneinfo ..."
rm -Rf /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1
mv /tmp/hems/dbus-systemcalc-py-dmanner-hems/opporunity-loads-standalone/tmp/zoneinfo /usr/lib/python3.12/site-packages/zoneinfo  > /dev/null 2>&1

echo " > Copying opportunity-lodas to standalone destination"
mkdir -p "/opt/victronenergy/opportunity-loads"
cp -Rf /tmp/hems/dbus-systemcalc-py-dmanner-hems/opportunity-loads-standalone/* /opt/victronenergy/opportunity-loads/

if [ "$num" -gt 0 ]; then
    echo " > Altering opportunityloads.py to enable FAKE-BMS usage with a count of $num"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = True' /opt/victronenergy/opportunity-loads/globals.py
    sed -Ei "s/^(\s+)self\.available_fake_bms\s*=.*/\\1self\.available_fake_bms = range(1, $num)/" /opt/victronenergy/opportunity-loads/opportunityloads.py
else
    echo " > Altering opportunityloads.py to disable FAKE-BMS usage"
    sed -Ei '/USE_FAKE_BMS\s*=\s*(True|False)/c\USE_FAKE_BMS = False' /opt/victronenergy/opportunity-loads/globals.py
fi

if [ "$num" -gt 0 ]; then
    echo " > Making FAKE-BMS service runnable..."
    chmod a+x /opt/victronenergy/opportunity-loads/tmp/fake_bms_service/run
    chmod 755 /opt/victronenergy/opportunity-loads/tmp/fake_bms_service/run

    echo " > Symlinking FAKE-BMS service as /service/fake_bms_service ..."
    ln -s /opt/victronenergy/opportunity-loads/tmp/fake_bms_service /service/fake_bms_service

    echo " > Adding FAKE-BMS service to rc.local..."
    line="ln -s /opt/victronenergy/opportunity-loads/tmp/fake_bms_service /service/fake_bms_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Altering fake_bms.py to enable FAKE-BMS usage with a count of $num"
    sed -Ei "s/^available_fake_bms\s*=.*/available_fake_bms = range(0, $num)/" /opt/victronenergy/opportunity-loads/tmp/fake_bms.py

    echo " > Starting FAKE-BMS service ..."
    svc -u /service/fake_bms_service
else
    echo " > Removing FAKE-BMS service from rc.local..."
    line="ln -s /opt/victronenergy/opportunity-loads/tmp/fake_bms_service /service/fake_bms_service"
    file="/data/rc.local"

    sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

    echo " > Stopic FAKE-BMS service if running..."
    svc -d /service/fake_bms_service
    svc -k /service/fake_bms_service

    echo " > Removing FAKE-BMS service if existing..."
    rm "/service/fake_bms_service"
fi

echo " > Stopic EVCS service if running..."
svc -d /service/evcs_s2_rm_service
svc -k /service/evcs_s2_rm_service

echo " > Removing EVCS Services if installed."
line="ln -s /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service /service/evcs_s2_rm_service"
file="/data/rc.local"
sed -i "/^$(printf '%s' "$line" | sed 's/[^^]/[&]/g; s/\^/\\^/g')$/d" "$file"

rm "/service/evcs_s2_rm_service"

if [ "$num_evcs" -eq 1 ]; then
    echo " > Installing asyncio_glib through PIP..."
    python -m pip install asyncio_glib > /dev/null 2>&1

    echo " > Setting UP EVCS Service (1 Phase)..."
    chmod a+x /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service/run
    chmod 755 /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service/run

    #set desired phases
    sed -Ei '/PHASE_MODE_CONFIG\s*=\s*([0-9])/c\PHASE_MODE_CONFIG = 1' /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm.py

    echo " > Symlinking EVCS Service as /service/evcs_s2_rm_service ..."
    ln -s /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service /service/evcs_s2_rm_service

    echo " > Adding EVCS Service  to rc.local..."
    line="ln -s /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service /service/evcs_s2_rm_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Starting EVCS Service ..."
    svc -u /service/evcs_s2_rm_service
fi

if [ "$num_evcs" -eq 3 ]; then
    echo " > Installing asyncio_glib through PIP..."
    python -m pip install asyncio_glib > /dev/null 2>&1
    
    echo " > Setting UP EVCS Service (3 Phase)..."
    chmod a+x /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service/run
    chmod 755 /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service/run

    #set desired phases
    sed -Ei '/PHASE_MODE_CONFIG\s*=\s*([0-9])/c\PHASE_MODE_CONFIG = 3' /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm.py

    echo " > Symlinking EVCS Service as /service/evcs_s2_rm_service ..."
    ln -s /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service /service/evcs_s2_rm_service

    echo " > Adding EVCS Service to rc.local..."
    line="ln -s /opt/victronenergy/opportunity-loads/tmp/evcs_s2_rm_service /service/evcs_s2_rm_service"
    file="/data/rc.local"
    grep -qxF "$line" "$file" || echo "$line" >> "$file"

    echo " > Starting EVCS Service ..."
    svc -u /service/evcs_s2_rm_service
fi

echo " > Symlinking Opportunity Loads Service as /service/opportunity-loads ..."
chmod a+x /opt/victronenergy/opportunity-loads/tmp/opportunity_loads_service/run
chmod 755 /opt/victronenergy/opportunity-loads/tmp/opportunity_loads_service/run
ln -s /opt/victronenergy/opportunity-loads/tmp/opportunity_loads_service /service/opportunity-loads

echo " > Adding Opportunity Loads Service to rc.local..."
line="ln -s /opt/victronenergy/opportunity-loads/tmp/opportunity_loads_service /service/opportunity-loads"
file="/data/rc.local"
grep -qxF "$line" "$file" || echo "$line" >> "$file"

echo " > Starting Opportunity Loads Service ..."
svc -u /service/opportunity-loads

echo ""
echo "==================================================================================================================="
echo ""
echo "Setup done. OpportunityLoads is left in a disabled state for now, if first install."
echo ""
