#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import uuid
import requests  #type:ignore
import dbus # type: ignore
from datetime import datetime, timedelta, timezone
import json
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

from vedbus import VeDbusService # type: ignore

if sys.version_info.major == 2:
    import gobject # type: ignore
else:
    from gi.repository import GLib as gobject # type: ignore

from dbus.mainloop.glib import DBusGMainLoop # type: ignore


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


DBusGMainLoop(set_as_default=True)

class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)

class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)
    
def dbusConnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()

class FakeBMS():
    def __init__(self, name, i):
        self.instance = i + 600
        self.dbusService = VeDbusService(name, bus=dbusConnection(), register=False)

        #Mgmt-Infos
        self.dbusService.add_path('/DeviceInstance', int(self.instance))
        self.dbusService.add_path('/Mgmt/ProcessName', __file__)
        self.dbusService.add_path('/Mgmt/ProcessVersion', 'Python ' + platform.python_version())
        self.dbusService.add_path('/Mgmt/Connection', "dbus")

        # Create the mandatory objects
        self.dbusService.add_path('/ProductId', 65535)
        self.dbusService.add_path('/ProductName', "HEMS Fake BMS {}".format(i)) 
        self.dbusService.add_path('/CustomName', "HEMS Fake BMS {}".format(i), writeable=True) 
        self.dbusService.add_path('/Latency', None)    
        self.dbusService.add_path('/FirmwareVersion', "1.0")
        self.dbusService.add_path('/HardwareVersion', "1.0")
        self.dbusService.add_path('/Connected', 1)
        self.dbusService.add_path('/Serial', "1337")

        self.dbusService.add_path('/Dc/0/Voltage', 0, writeable=True)
        self.dbusService.add_path('/Dc/0/Power', 0, writeable=True)
        self.dbusService.add_path('/Dc/0/Current', 0, writeable=True)
        self.dbusService.add_path('/Soc', 0, writeable=True)

        self.dbusService.register()
        
if __name__ == "__main__":
    def main():
        #Creates 10 Fake BMS that can be populated by HEMS to display Consumer-Information. 
        #Set USE_FAKE_BMS to true in HEMS when using. 
        services = []
        for i in [0,1,2,3,4,5,6,7,8,9]:
            service = FakeBMS('com.victronenergy.battery.hems_fake_{}'.format(i), i)
            services.append(service)
        
        mainloop = gobject.MainLoop()
        mainloop.run()       
    
    main()
