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

All mocks will log to `/data/log/S2/{filename}`
