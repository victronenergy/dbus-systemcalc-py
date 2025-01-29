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
                type=str, default="com.victronenergy.multi.socketcan_can0_vi0_uc123456")
parser.add_argument("-p", "--phase", help="the AC phase the unit is installed on",
	type=int, default=1)

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

serial = 'HQ2108RHQJZ' + str(args.phase)
s = DbusDummyService(
    servicename=args.name+str(args.phase),
    deviceinstance=args.phase-1,
    paths={
		'/Ac/ActiveIn/ActiveInput': {'initial': 0},
		'/Ac/Control/IgnoreAcIn1': {'initial': 0},
		'/Ac/In/1/CurrentLimit': {'initial': 10},
		'/Ac/In/1/CurrentLimitIsAdjustable': {'initial': 1},
		'/Ac/In/2/CurrentLimit': {'initial': None},
		'/Ac/In/2/CurrentLimitIsAdjustable': {'initial': 0},
		'/Ac/In/1/L{}/F'.format(args.phase): {'initial': 50},
		'/Ac/In/1/L{}/I'.format(args.phase): {'initial': 0.1},
		'/Ac/In/1/L{}/P'.format(args.phase): {'initial': 22},
		'/Ac/In/1/L{}/V'.format(args.phase): {'initial': 229.9},
		'/Ac/In/1/Type': {'initial': 1},
		'/Ac/NumberOfAcInputs': {'initial': 1},
		'/Ac/NumberOfPhases': {'initial': 1},
		'/Ac/Out/L{}/F'.format(args.phase): {'initial': 50},
		'/Ac/Out/L{}/I'.format(args.phase): {'initial': 0},
		'/Ac/Out/L{}/P'.format(args.phase): {'initial': -8},
		'/Ac/Out/L{}/V'.format(args.phase): {'initial': 229.8},
		'/Ac/PvInverterAvailable': {'initial': 0},
		'/Alarms/HighTemperature': {'initial': 0},
		'/Alarms/HighVoltage': {'initial': 0},
		'/Alarms/HighVoltageAcOut': {'initial': 0},
		'/Alarms/LowSoc': {'initial': 0},
		'/Alarms/LowVoltage': {'initial': 0},
		'/Alarms/LowVoltageAcOut': {'initial': 0},
		'/Alarms/Overload': {'initial': 0},
		'/Alarms/Ripple': {'initial': 0},
		'/Alarms/ShortCircuit': {'initial': 0},
		'/Alarms/GridLost': {'initial': 0},
		'/CustomName': {'initial': 'RS 48/6000/100 ' + serial},
		'/Capabilities/HasAcPassthroughSupport': {'initial': 1},
		'/Dc/0/Power': {'initial': 12},
		'/Dc/0/Current': {'initial': 0.2},
		'/Dc/0/RippleVoltage': {'initial': 0.019},
		'/Dc/0/Temperature': {'initial': None},
		'/Dc/0/Voltage': {'initial': 54},
		'/DeviceOffReason': {'initial': 1024},
		'/Devices/0/CustomName': {'initial': 'RS 48/6000/100 ' + serial},
		'/Devices/0/Nad': {'initial': 64},
		'/Devices/0/ServiceName': {'initial': 'com.victronenergy.multi.socketcan_can0_vi0_uc162268'},
		'/Devices/0/Gateway': {'initial': 'socketcan:can0'},
		'/Devices/0/Nad': {'initial': 36 + args.phase - 1},
		'/Energy/AcIn1ToAcOut': {'initial': 0.26},
		'/Energy/AcIn1ToInverter': {'initial': 117.05},
		'/Energy/AcOutToAcIn1': {'initial': 0.08},
		'/Energy/InverterToAcIn1': {'initial': 0},
		'/Energy/InverterToAcOut': {'initial': 0},
		'/Energy/OutToInverter': {'initial': 24.24},
		'/Energy/SolarToAcIn1': {'initial': 0},
		'/Energy/SolarToAcOut': {'initial': 0},
		'/Energy/SolarToBattery': {'initial': 0},
		'/ErrorCode': {'initial': 0},
		'/GroupId': {'initial': 11},
		'/IsInverterCharger': {'initial': 1},
		'/Link/ChargeCurrent': {'initial': 100},
		'/Link/DischargeCurrent': {'initial': 600},
		'/Mode': {'initial': 3},
		'/MppOperationMode': {'initial': 0},
		'/N2kDeviceInstance': {'initial': 0},
		'/N2kSystemInstance': {'initial': 1},
		'/N2kUniqueNumber': {'initial': 162268},
		'/NrOfTrackers': {'initial': 1},
		'/Pv/V': {'initial': 0.59},
		'/Relay/0/State': {'initial': 1},
		'/Serial': {'initial': '0162268 ' + serial},
		'/Settings/AlarmLevel/HighTemperature': {'initial': 1},
		'/Settings/AlarmLevel/HighVoltage': {'initial': 1},
		'/Settings/AlarmLevel/HighVoltageAcOut': {'initial': 1},
		'/Settings/AlarmLevel/LowSoc': {'initial': 1},
		'/Settings/AlarmLevel/LowVoltage': {'initial': 1},
		'/Settings/AlarmLevel/LowVoltageAcOut': {'initial': 1},
		'/Settings/AlarmLevel/Overload': {'initial': 1},
		'/Settings/AlarmLevel/Ripple': {'initial': 1},
		'/Settings/BmsPresent': {'initial': 1},
		'/Settings/ChargeCurrentLimit': {'initial': 100},
		'/Soc': {'initial': 97.34},
		'/State': {'initial': 252},
		'/TimeToGo': {'initial': None},
		'/Yield/Power': {'initial': 0},
		'/Yield/System': {'initial': 49.04},
		'/Yield/User': {'initial': 48.39},

		# ESS
		'/Settings/Ess/MinimumSocLimit': {'initial': 20 },
        '/Settings/Ess/Mode': {'initial': 1},
		'/Ess/DisableFeedIn': {'initial': 0},
        '/Ess/AcPowerSetpoint': {'initial': 0},

    },
    productname='Inverter RS Smart 48V/6000VA/80A',
    connection='VE.Can')

logger.info('Connected to dbus, and switching over to GLib.MainLoop() (= event based)')
mainloop = GLib.MainLoop()
mainloop.run()
