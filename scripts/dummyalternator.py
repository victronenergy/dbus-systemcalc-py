#!/usr/bin/env python3

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
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
                type=str, default="com.victronenergy.alternator.ttyUSB1")
parser.add_argument("-i", "--instance", help="the DeviceInstance for the service",
				type=int, default=0)
parser.add_argument("-p", "--product-id", help="the ProductId for the service",
				type=lambda x: int(x, 0), default=0xA3F1)

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=args.instance,
    productid=args.product_id,
    paths={
		'/CustomName': {'initial': 'Orion XS 1400 HQ2501ORION'},
		'/Dc/0/Current': {'initial': 0},
		'/Dc/0/Temperature': {'initial': None},
		'/Dc/0/Voltage': {'initial': 13.5},
		'/Dc/In/I': {'initial': 20.0},
		'/Dc/In/P': {'initial': 256},
		'/Dc/In/V': {'initial': 12.8},
		'/DeviceOffReason': {'initial': 0},
		'/Devices/0/CustomName': {'initial': 'Orion XS 1400 HQ2501ORION'},
		'/Devices/0/ServiceName': {'initial': 'com.victronenergy.alternator.ttyUSB1'},
		'/ErrorCode': {'initial': 0},
		'/GroupId': {'initial': 41},
		'/Link/BatteryCurrent': {'initial': None},
		'/Link/ChargeCurrent': {'initial': None},
		'/Link/ChargeVoltage': {'initial': 14.2},
		'/Link/CurrentSenseActive': {'initial': 0},
		'/Link/NetworkMode': {'initial': 0},
		'/Link/NetworkStatus': {'initial': 4},
		'/Link/TemperatureSense': {'initial': None},
		'/Link/TemperatureSenseActive': {'initial': 0},
		'/Link/VoltageSense': {'initial': None},
		'/Link/VoltageSenseActive': {'initial': 0},
		'/Mode': {'initial': 1},
		'/Serial': {'initial': 'HQ2501ORION'},
		'/Settings/BmsPresent': {'initial': 0},
		'/Settings/ChargeCurrentLimit': {'initial': 10},
        '/Settings/OutputBattery': {'initial': 0},
		'/State': {'initial': 3},
		'/History/Cumulative/User/OperationTime': {'initial': 1234},
		'/History/Cumulative/User/ChargedAh': {'initial': 445},
    },
    productname='Orion XS 1400 Charger',
    connection='VE.Direct port 1')

logger.info('Connected to dbus, and switching over to GLib.MainLoop() (= event based)')
mainloop = GLib.MainLoop()
mainloop.run()
