#!/usr/bin/env python3

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from functools import partial
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

def loop(start, offset, path, value):
    return start + 1 + (value-start) % offset

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=1,
    paths={
        '/State': {'initial': 242},
        '/Dc/0/Voltage': {'initial': 41.0, 'update': partial(loop, 41, 20)},
        '/Dc/0/Current': {'initial': 42.0, 'update': partial(loop, 42, 20)},
        '/Dc/0/Temperature': {'initial': 5, 'update': partial(loop, 5, 20)},

        '/Pv/I': {'initial': 0.0, 'update': partial(loop, 0, 20)},
        '/Pv/V': {'initial': 80.0, 'update': partial(loop, 80, 20)},
        '/Yield/Power': {'initial': 800, 'update': partial(loop, 800, 20)},
        '/History/Daily/0/TimeInBulk': {'initial': 0, 'update': 1},

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

logger.info('Connected to dbus, and switching over to GLib.MainLoop() (= event based)')
mainloop = GLib.MainLoop()
mainloop.run()
