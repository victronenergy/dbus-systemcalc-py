#!/usr/bin/env python

# takes data from the dbus, does calculations with it, and puts it back on
from dbus.mainloop.glib import DBusGMainLoop
import gobject
import argparse
import logging
import sys
import os

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '../ext/velib_python'))
from dbusdummyservice import DbusDummyService

# Argument parsing
parser = argparse.ArgumentParser(
    description='dummygridmeter.py demo run'
)

parser.add_argument(
    "-n", "--name", help="the D-Bus service you want me to claim", type=str,
    default="com.victronenergy.grid.ttyUSB0")

parser.add_argument(
    "-p", "--position", help="position (and instance): 0=grid, 1=output, 2=genset", type=int,
    default="0")

args = parser.parse_args()

# Init logging
logging.basicConfig(level=logging.DEBUG)
logging.info(__file__ + " is starting up, use -h argument to see optional arguments")

# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
DBusGMainLoop(set_as_default=True)

pvac_output = DbusDummyService(
    servicename=args.name,
    deviceinstance=args.position,
    productname='Grid meter (dummy)',
    paths={
        '/Ac/L1/Power': {'initial': 150},
        '/Ac/L2/Power': {'initial': 200},
        '/Ac/L3/Power': {'initial': 250},
        '/Ac/Power': {'initial': 600}})

print 'Connected to dbus, and switching over to gobject.MainLoop() (= event based)'
mainloop = gobject.MainLoop()
mainloop.run()
