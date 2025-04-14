#!/usr/bin/env python
 
# imports
import configparser # for config/ini file
import datetime
from logging.handlers import TimedRotatingFileHandler
import signal
import sys
import os
import logging
import os
import platform
import threading
import time
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
import ssl
import dbus # type: ignore

if sys.version_info.major == 2:
    import gobject # type: ignore
else:
    from gi.repository import GLib as gobject # type: ignore

import paho.mqtt.client as mqtt # type: ignore
from gi.repository import GLib # type: ignore
# victronr
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')
from vedbus import VeDbusService # type: ignore
from dbusmonitor import DbusMonitor # type: ignore
from dbus.mainloop.glib import DBusGMainLoop # type: ignore
from s2_rm import S2ResourceManagerItem

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)

class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)
    
def dbusConnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()

class ShellyS2Mock:
    def __init__(self):

        #Create the service on dbus, so EMS can detect this device. 
        self.serviceType = "com.victronenergy.s2Mock"
        self.serviceName = self.serviceType + ".hardcodedShellyMock"
        self.dbusService = VeDbusService(self.serviceName, bus=dbusConnection(), register=False)
        
        #Mgmt-Infos
        self.dbusService.add_path('/DeviceInstance', 768)
        self.dbusService.add_path('/Mgmt/ProcessName', __file__)
        self.dbusService.add_path('/Mgmt/ProcessVersion', '1.0 on Python ' + platform.python_version())
        self.dbusService.add_path('/Mgmt/Connection', "dbus")

        # Create the mandatory objects
        self.dbusService.add_path('/ProductId', 65535)
        self.dbusService.add_path('/ProductName', "Shelly S2 Mock") 
        self.dbusService.add_path('/CustomName', "Heater L1") 
        self.dbusService.add_path('/Latency', None)    
        self.dbusService.add_path('/FirmwareVersion', "1.0")
        self.dbusService.add_path('/HardwareVersion', "1.0")
        self.dbusService.add_path('/Connected', 1)
        self.dbusService.add_path('/Serial', "1337")
        
        self.dbusService.register()

        self._timer = GLib.timeout_add(1000, self._on_timer)
        pass

    def _on_timer(self):
        print("Looping...")
        return True

def main():
    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    from dbus.mainloop.glib import DBusGMainLoop # type: ignore
    DBusGMainLoop(set_as_default=True)

    mock = ShellyS2Mock()
    
    mainloop = gobject.MainLoop()
    mainloop.run()            

    sys.exit(0)    

if __name__ == "__main__":
    try:
        main()
    except Exception as uncoughtException:
        logging.error("Error in ShellyS2Mock", exc_info=uncoughtException)