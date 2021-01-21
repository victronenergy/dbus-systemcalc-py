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
parser = argparse.ArgumentParser(description='dummy dbus service')

parser.add_argument("-n", "--name", help="the D-Bus service you want me to claim",
				type=str, default="com.victronenergy.vebus.ttyO1")
parser.add_argument("-i", "--instance", help="the device instance number",
				type=int, default=0)

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(servicename=args.name, deviceinstance=args.instance, paths={
		'/Ac/ActiveIn/P': {'initial': 0},
		'/Ac/ActiveIn/L1/P': {'initial': 0},
		'/Ac/ActiveIn/ActiveInput': {'initial': 0},
		'/Ac/ActiveIn/Connected': {'initial': 1},
		'/Ac/Out/P': {'initial': 0},
		'/Ac/Out/L1/P': {'initial': 0},
		'/Ac/NumberOfPhases': {'initial': 1},
		'/Ac/State/IgnoreAcIn1': {'initial': 0},
		'/Ac/State/IgnoreAcIn2': {'initial': 0},
		'/Alarms/HighTemperature': {'initial': 0},
		'/Alarms/L1/HighTemperature': {'initial': 0},
		'/Alarms/L1/LowBattery': {'initial': 0},
		'/Alarms/L1/Overload': {'initial': 0},
		'/Alarms/L1/Ripple': {'initial': 0},
		'/Alarms/LowBattery': {'initial': 0},
		'/Alarms/Overload': {'initial': 0},
		'/Alarms/Ripple': {'initial': 0},
		'/Alarms/TemperatureSensor': {'initial': 0},
		'/Alarms/VoltageSensor': {'initial': 0},
		'/Alarms/GridLost': {'initial': 0},
		'/Alarms/HighDcVoltage': {'initial': 0},
		'/Alarms/HighDcCurrent': {'initial': 0},
		'/Dc/0/Voltage': {'initial': 11},
		'/Dc/0/Current': {'initial': 12},
		'/Dc/0/MaxChargeCurrent': {'initial': None},
		'/Dc/0/Temperature': {'initial': None},
		'/Devices/0/Assistants': {'initial': [0]*56},
		'/Devices/0/ExtendStatus/GridRelayReport/Code': {'initial': None},
		'/Devices/0/ExtendStatus/GridRelayReport/Count': {'initial': None},
		'/Devices/0/ExtendStatus/WaitingForRelayTest': {'initial': 0},
		'/ExtraBatteryCurrent': {'initial': 0},
		'/FirmwareFeatures/BolFrame': {'initial': None},
		'/FirmwareFeatures/BolUBatAndTBatSense': {'initial': None},
		'/Soc': {'initial': 10},
		'/State': {'initial': None},
		'/Mode': {'initial': None},
		'/VebusMainState': {'initial': None},
		'/Hub/ChargeVoltage': {'initial': None},
		'/Hub4/AssistantId': {'initial': None},
		'/Hub4/Sustain': {'initial': None},
		'/Hub4/L1/AcPowerSetpoint': {'initial': None},
		'/Hub4/DisableFeedIn': {'initial': None},
		'/Hub4/TargetPowerIsMaxFeedIn': {'initial': 0},
		'/Hub4/FixSolarOffsetTo100mV': {'initial': 0},
		'/BatteryOperationalLimits/MaxChargeVoltage': {'initial': None},
		'/BatteryOperationalLimits/MaxChargeCurrent': {'initial': None},
		'/BatteryOperationalLimits/MaxDischargeCurrent': {'initial': None},
		'/BatteryOperationalLimits/BatteryLowVoltage': {'initial': None}},
	productname='Multi 12/3000',
	connection='CCGX-VE.Bus port')

logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()
