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
HEMS is initially left in a disabled state, as there is not yet anything todo. 

# Configure Battery Reservation Equation
This can be a simple (fixed) number, or any legit python expression. `SOC` is a wildcard that can be used to create SoC-Dependent reservations.

Examples: 

Fixed Value:
`dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "5000"`

10.000, lowered by 100 per SOC gained:
`dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "10000 - SOC * 100"`

5.000, lowered by 50 per SOC gained and already 0 when SOC is reaching 98%:
`dbus -y com.victronenergy.settings /Settings/HEMS/BatteryReservationEquation SetValue "5000 - SOC * 50 if SOC < 98 else 0"`

# Enable Hems
`dbus -y com.victronenergy.settings /Settings/HEMS/Mode SetValue 1`

# Configure Shelly Mocks
In the file `/opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Multi.py`, line 393, you can add your shellies. 
The mock uses http (no auth) and works with any gen2+ shelly.

![image](https://github.com/user-attachments/assets/dedcc3f6-b2b4-410d-9675-49fe809dec2f)

# Usefull information
The debug environment is creating more logging than usual: 
`/data/log/S2/hems.py_info.log` - Just incremental logging. Good to review a days events.
`/data/log/S2/hems.py_debug.log` - Debug log. Nough said :-)
`/data/log/S2/shellyMock_OMBC_Multi.py.log` - logs of the mock service
