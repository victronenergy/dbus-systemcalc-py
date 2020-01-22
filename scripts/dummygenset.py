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
                type=str, default="com.victronenergy.genset.socketcan_can0")

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=0,
    paths={
		'/AutoStart': {'initial': 1},
		'/CustomName': {'initial': None},
		'/StatusCode': {'initial': 0},
		'/ErrorCode': {'initial': 0},
		'/Engine/Load': {'initial': 65},
		'/Engine/Speed': {'initial': 1800},
		'/Engine/OperatingHours': {'initial': 101},
		'/Engine/CoolantTemperature': {'initial': 92},
		'/Engine/WindingTemperature': {'initial': 101},
		'/Engine/ExaustTemperature': {'initial': 188},
		'/StarterVoltage': {'initial': 12.2},
		'/Ac/L1/Voltage': {'initial': 230.2},
		'/Ac/L1/Current': {'initial': 1.2},
		'/Ac/L1/Power': {'initial': 480.1},
		'/Ac/L1/Frequency': {'initial': 50.1},
		'/Ac/L2/Voltage': {'initial': 231.2},
		'/Ac/L2/Current': {'initial': 1.3},
		'/Ac/L2/Power': {'initial': 481.1},
		'/Ac/L2/Frequency': {'initial': 50.1},
		'/Ac/L3/Voltage': {'initial': 229.2},
		'/Ac/L3/Current': {'initial': 1.1},
		'/Ac/L3/Power': {'initial': 481.1},
		'/Ac/L3/Frequency': {'initial': 50.1},
    },
    productname='Generic Genset',
    connection='CAN-bus')
s._dbusservice['/ProductId'] = 0xB040

logger.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()
