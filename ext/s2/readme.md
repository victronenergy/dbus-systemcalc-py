### Before Setup

HEMS debug mode supports to use a FAKE-BMS-Service to get visualization into VRM. 
It creates a number of BMS-Services, which have their `CustomName`, `Soc`, and `Dc/0/Power` Value writeable.

When enabling, three configurations in the gx should be performed: 

1.) Make sure you select your actual BMS in DVCC: 

<img src="https://github.com/user-attachments/assets/c642b57d-03d8-4174-8365-c65c7d5aecba" width="500">

2.) Make sure you select your actual BMS as primary Battery-Monitor: 

<img src="https://github.com/user-attachments/assets/395caa5d-51fe-462b-92b6-f058fc0aefca" width="500">

3.) Select which Fake BMS you want to enable to be displayed in VRM:

<img src="https://github.com/user-attachments/assets/586b8eb5-2ef5-4106-9de5-0ffa1ec6c826" width="500">

Example: 
VRM now outlines every controllable consumer and some information about the state as seen by hems,
the Battery Reservation BMS displays the following values: 

<img src="https://github.com/user-attachments/assets/8d65359d-0c55-4dfb-8f8f-eac94f2ff9a1" width="800">

### Setup HEMS

Run the following lines on your gx: 
This will install and/or update your HEMS and required dependencies.

```
rm -f /tmp/update_hems.sh
wget -P /tmp https://github.com/victronenergy/dbus-systemcalc-py/raw/refs/heads/dmanner/hems/ext/s2/update_hems.sh
chmod a+x /tmp/update_hems.sh
chmod 755 /tmp/update_hems.sh
/tmp/update_hems.sh
```

### After Setup
# Restart systemcalc
After setup, you need to restart systemcalc, so the required config paths are created. 

```
svc -t /service/dbus-systemcalc-py
```

HEMS is initially left in a disabled state, as there is not yet anything todo. 

# Configure Battery Reservation Equation
This can be a simple (fixed) number, or any legit python expression. `SOC` is a wildcard that can be used to create SoC-Dependent reservations.

Examples: 

Fixed Value:
```
dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "5000"
```

10.000, lowered by 100 per SOC gained:
```
dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "10000 - SOC * 100"
```

5.000, lowered by 50 per SOC gained and already 0 when SOC is reaching 98%:
```
dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "5000 - SOC * 50 if SOC < 98 else 0"
```

# Configure Continuous Inverter Power
Total per phase, made a setting, so it can be set depending on various environment conditions. 
Default value tbd.
```
dbus -y com.victronenergy.settings /Settings/HEMS/ContinuousInverterPower SetValue 4000
```

# Enable Hems
```
dbus -y com.victronenergy.settings /Settings/HEMS/Mode SetValue 1
```

# Configure Shelly Mocks
In the file `/opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi.py`, line 393, you can add your shellies. 
The mock uses http (no auth) and works with any gen2+ shelly.

![image](https://github.com/user-attachments/assets/dedcc3f6-b2b4-410d-9675-49fe809dec2f)

### Usefull information / commands
# Logs
The debug environment is creating more logging than usual: 
- `/data/log/S2/hems.py_info.log` - Just incremental logging. Good to review a days events.
- `/data/log/S2/hems.py_debug.log` - Debug log. Nough said :-)
- `/data/log/S2/shellyMock_OMBC_Multi.py.log` - logs of the mock service

# Restart Mock
do a 
```
ps | greb OMBC
```
followed by `kill PID` - the service will restart the mock process. 

![image](https://github.com/user-attachments/assets/988ada2b-fa6d-42af-9827-0eb392d7f93e)

# Fine Tuning with operational constraints
Shelly RMs are "unified", so they run without constraints. To account for this, and give a little bit of control,
each RM will create a path like `/switch/815/Devices/0/S2/Auto` that is writable.

While it's final purpose is to add a Auto/Off Toggle in VRM, you can ofc. use anything (dbus, mqtt, nodered) to turn that Value from 1 to 0. 
- When the value is `1`, the RM will request the unified On-Off-OMBC-Control, and HEMS will accept and manage the consumer. 
- When the value is `0`, the RM will only request NOCONTROL, HEMS will accept and NOT control that consumer. 

For example, i'm using a powershell script on my server to turn the heaters to "NOCONTROL" as soon as a certain temperature is reached,
or i'm setting them into manual mode on purpose:

```
#Heater Values
$rodTemp = Get-ESVar -Key "Devices/d1Watering1/Sensors/HEATING_ROD_TEMP/Value" -AsDouble
$resTemp = Get-ESVar -Key "Devices/EbusEvaluator/Sensors/ReservoirMiddle/Value" -AsDouble
$manualMode = Get-ESVar -Key "Devices/shellyPro2PMPVHeat1/IO/ManualOverride/State" -AsBoolean

#Heater L1 Automatic, that is RM 1 - allowed upto 70/90°
if (!$manualMode -and $resTemp -lt 70 -and $rodTemp -lt 90){
    $global:cerboMqttClient.Publish("W/c0619ab4a585/switch/815/Devices/1/S2/Auto", (Encode "{""value"":1}"))
}else{
    $global:cerboMqttClient.Publish("W/c0619ab4a585/switch/815/Devices/1/S2/Auto", (Encode "{""value"":0}"))
}
...
```
