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
                type=str, default="com.victronenergy.vebus.ttyO1")

args = parser.parse_args()

print(__file__ + " is starting up, use -h argument to see optional arguments")
logger = setup_logging(debug=True)

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

s = DbusDummyService(
    servicename=args.name,
    deviceinstance=0,
    paths={
        '/Mode': {'initial': 3},
        '/State': {'initial': None},
        '/Soc': {'initial': 10},
        '/Dc/V': {'initial': 11},
        '/Dc/I': {'initial': 12},
        '/Dc/P': {'initial': 131},
        '/Ac/ActiveIn/ActiveInput' : {'initial' : 0},
        '/Ac/ActiveIn/L1/P': {'initial': 500},
        '/Ac/ActiveIn/L2/P': {'initial': 400},
        '/Ac/ActiveIn/L3/P': {'initial': 300},
        '/Ac/Out/L1/P': {'initial': 300},
        '/Ac/Out/L2/P': {'initial': 300},
        '/Ac/Out/L3/P': {'initial': 300},
        # '/Hub4/AcPowerSetpoint': {'initial': 100}
        },
    productname='Multi 12/3000',
    connection='CCGX-VE.Bus port')

logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
mainloop = gobject.MainLoop()
mainloop.run()




