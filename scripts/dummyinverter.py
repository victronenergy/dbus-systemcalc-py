#!/usr/bin/env python

from dbus.mainloop.glib import DBusGMainLoop
import gobject
import argparse
import logging
import sys
import os

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '../ext/velib_python'))
from dbusdummyservice import DbusDummyService
from logger import setup_logging

# Argument parsing
parser = argparse.ArgumentParser(
    description='dummy dbus service'
)

parser.add_argument("-n", "--name", help="the D-Bus service you want me to claim",
                type=str, default="com.victronenergy.inverter.ttyO1")

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=0,
    paths={
		'/Serial': {'initial': None},
		'/CustomName': {'initial': None},
		'/GroupId': {'initial': None},
		'/IsInverterCharger': {'initial': 0},
		'/Alarms/LowVoltage': {'initial': 0},
		'/Alarms/HighVoltage': {'initial': 0},
		'/Alarms/LowTemperature': {'initial': 0},
		'/Alarms/HighTemperature': {'initial': 0},
		'/Alarms/Overload': {'initial': 0},
		'/Alarms/Ripple': {'initial': 0},
		'/Alarms/LowVoltageAcOut': {'initial': 0},
		'/Alarms/HighVoltageAcOut': {'initial': 0},
		'/Dc/0/Voltage': {'initial': 50},
		'/Dc/0/Current': {'initial': 4.1},
		'/Ac/Out/L1/V': {'initial': 230},
		'/Ac/Out/L1/I': {'initial': 0.9},
		'/Ac/Out/L1/P': {'initial': 180},
		'/Ac/Out/L1/S': {'initial': 200},
		'/Ac/Out/L1/F': {'initial': 50},
		'/Mode': {'initial': 2},
		'/State': {'initial': 9},
		'/Relay/0/State': {'initial': 0},
		'/Pv/V': {'initial': 381},
		'/Yield/Power': {'initial': 123},
		'/DeviceOffReason': {'initial': 0},
		'/Soc': {'initial': 10},
		'/Energy/InverterToAcOut': {'initial': None},
		'/Energy/OutToInverter': {'initial': None},
		'/Energy/SolarToBattery': {'initial': None},
		'/Energy/SolarToAcOut': {'initial': None},
    },
    productname='Inverter RS Smart 48V/6000VA/80A',
    connection='VE.Direct port 1')

logger.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()
