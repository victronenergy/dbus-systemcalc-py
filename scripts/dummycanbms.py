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

parser.add_argument("-n", "--name",
    help="the D-Bus service you want me to claim",
    type=str, default="com.victronenergy.battery.socketcan_can0")

parser.add_argument("-i", "--instance",
	help="DeviceInstance",
	type=int, default=0)

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=args.instance,
    paths={
        '/Alarms/CellImbalance': {'initial': 0},
        '/Alarms/HighChargeCurrent': {'initial': 0},
        '/Alarms/HighChargeTemperature': {'initial': 0},
        '/Alarms/HighDischargeCurrent': {'initial': 0},
        '/Alarms/HighTemperature': {'initial': 0},
        '/Alarms/HighVoltage': {'initial': 0},
        '/Alarms/InternalFailure': {'initial': 0},
        '/Alarms/LowChargeTemperature': {'initial': 0},
        '/Alarms/LowTemperature': {'initial': 0},
        '/Alarms/LowVoltage': {'initial': 0},

        '/Soc': {'initial': 40},
        '/Dc/0/Voltage': {'initial': 25},
        '/Dc/0/Current': {'initial': 20},
        '/Dc/0/Power': {'initial': 500},
        '/Dc/0/Temperature': {'initial': 23.8},
        '/Info/BatteryLowVoltage': {'initial': 23},
        '/Info/MaxChargeCurrent': {'initial': 600},
        '/Info/MaxChargeVoltage': {'initial': 28.4},
        '/Info/MaxDischargeCurrent': {'initial': 600},
        '/System/MinCellVoltage': {'initial': None},
        '/System/MaxCellVoltage': {'initial': None},
        '/System/NrOfModulesBlockingCharge': {'initial': 0},
        '/System/NrOfModulesBlockingDischarge': {'initial': 0},
        '/System/NrOfModulesOffline': {'initial': 0},
        '/System/NrOfModulesOnline': {'initial': 2},
    },
    productname='ACME BMS battery {}'.format(args.instance),
    connection='CAN-bus')

logger.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()




