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
                type=str, default="com.victronenergy.solarcharger.ttyO1")

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=0,
    paths={
        '/State': {'initial': 242},
        '/Dc/0/Voltage': {'initial': 41},
        '/Dc/0/Current': {'initial': 42},
        '/Dc/0/Temperature': {'initial': None},
        '/Link/NetworkMode': {'initial': None},
        '/Link/NetworkStatus': {'initial': None},
        '/Link/ChargeVoltage': {'initial': None},
        '/Link/ChargeCurrent': {'initial': None},
        '/Settings/ChargeCurrentLimit': {'initial': 70},
        '/Settings/BmsPresent': {'initial': None},
        '/DeviceOffReason': {'initial': 0},
    },
    productname='Solarcharger',
    connection='VE.Direct port 1')

logger.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()




