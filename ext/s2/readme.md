Required dependencies:

(if beta release)
`/opt/victronenergy/swupdate-scripts/set-feed.sh candidate`

Always
`opkg update`
`opkg install python3-pip`

pip stuff
`python -m pip install s2-python`
`python -m pip install tzdata`

Missing module "zoneinfo" (dev env):
1.) Download https://www.python.org/ftp/python/3.12.0/Python-3.12.0.tgz
2.) Copy `Python-3.12.0/Lib/zoneinfo` to `/usr/lib/python3.12/site-packages/`

From repository to cerbo:
`dbus_systemcalc.py` (make sure to Upload with CR only, not CRLF)
`delegates/__init__.py`
`delegates/hems.py`
`ext/aiovelib/*`
`ext/s2/*`