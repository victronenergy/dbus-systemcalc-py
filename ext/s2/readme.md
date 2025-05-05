Required dependencies:

(if beta release)
```
/opt/victronenergy/swupdate-scripts/set-feed.sh candidate
```

Always
```
opkg update
opkg install python3-pip
```

pip stuff
```
python -m pip install s2-python
python -m pip install tzdata
```

Missing module "zoneinfo" (dev env):

1.) Download https://www.python.org/ftp/python/3.12.0/Python-3.12.0.tgz

2.) Copy `Python-3.12.0/Lib/zoneinfo` to `/usr/lib/python3.12/site-packages/`

3.) Alternatively copy `.../ext/s2/zoneinfo` to `/usr/lib/python3.12/site-packages/`

From repository to cerbo:
```
dbus_systemcalc.py (make sure to Upload with CR only, not CRLF)
delegates/__init__.py
delegates/hems.py
ext/aiovelib/*
ext/s2/*
```

Finally, download the latest `s2-python` from github: [src/s2python](https://github.com/flexiblepower/s2-python/archive/refs/heads/main.zip)
and copy `src/s2python` to `usr/lib/python3.12/site-packages`

To setup a mock as service: 
```
chmod a+x /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Dual_heaterL1L2_service/run
chmod 755 /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Dual_heaterL1L2_service/run
ln -s /opt/victronenergy/dbus-systemcalc-py/ext/s2/shellyMock_OMBC_Dual_heaterL1L2_service /service/shellyMock_OMBC_Dual_heaterL1L2_service
```

(last line probably shoud be added to /data/rc.local as well.)

All mocks will log to `/data/log/S2/{filename}`

### Fake BMS ###
For development purpose, I've created the service `fake_bms`. It creates a number of BMS-Services, which have their `CustomName`, `Soc`, and `Dc/0/Power` Value writeable. 
The number of Fake-BMS to be created can be adjusted in the file and should be adjusted in the delegate as well. (Simple lists like `[0,1,2,3,...]` where bms 0 is used for
representation of the battery reservation active. 

This is only used if `USE_FAKE_BMS` inside `hems.py` is set to true. 

When enabling, three configurations in the gx should be performed: 

1.) Make sure you select your actual BMS in DVCC: 

![image](https://github.com/user-attachments/assets/c642b57d-03d8-4174-8365-c65c7d5aecba)

2.) Make sure you select your actual BMS as primary Battery-Monitor: 

![image](https://github.com/user-attachments/assets/395caa5d-51fe-462b-92b6-f058fc0aefca)

3.) Select which Fake BMS you want to enable to be displayed in VRM:

![image](https://github.com/user-attachments/assets/586b8eb5-2ef5-4106-9de5-0ffa1ec6c826)

Example: 
VRM now outlines every controllable consumer and some information about the state as seen by hems: 

![image](https://github.com/user-attachments/assets/1b5aa49f-f11c-47ae-b167-70f75737b032)
