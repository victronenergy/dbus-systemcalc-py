#!/usr/bin/env python

""" This script contains a huge amount of hackery. Its purpose is to proxy
    a lesser battery monitor, replicate the power-related paths, but
    add on BMS-paths. This allows BMS testing while still using real values
    from another battery monitor, such as a BMV. """

import argparse
import logging
import sys
import os
from functools import partial
from dbus.mainloop.glib import DBusGMainLoop
import dbus
import gobject

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '../ext/velib_python'))
from dbusdummyservice import DbusDummyService

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dbus_int_types = (dbus.Int32, dbus.UInt32, dbus.Byte, dbus.Int16, dbus.UInt16,
        dbus.UInt32, dbus.Int64, dbus.UInt64)

def unwrap_dbus_value(val):
    """Converts D-Bus values back to the original type. For example if val is
       of type DBus.Double, a float will be returned."""
    if isinstance(val, dbus_int_types):
        return int(val)
    if isinstance(val, dbus.Double):
        return float(val)
    return val

def set_state(callback, v):
    value = unwrap_dbus_value(v["Value"])
    callback(value)

def query(conn, service, path):
    return conn.call_blocking(service, path, None, "GetValue", '', [])

def track(conn, service, path, callback):
    value = unwrap_dbus_value(query(conn, service, path))
    callback(value)

    # And track it
    conn.add_signal_receiver(partial(set_state, callback),
            dbus_interface='com.victronenergy.BusItem',
            signal_name='PropertiesChanged',
            path=path,
            bus_name=service)

def _set_value(service, path, v):
    service._dbusservice[path] = round(v, 2)

def main():
    # Argument parsing
    parser = argparse.ArgumentParser(
        description='dummy dbus service'
    )

    parser.add_argument("-n", "--name",
        help="the D-Bus service you want me to claim",
        type=str, default="com.victronenergy.battery.socketcan_can0")

    parser.add_argument("parent", help="battery service to proxy for",
        type=str, default="com.victronenergy.battery.ttyO0")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    s = DbusDummyService(
        servicename=args.name,
        deviceinstance=0,
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

            '/Soc': {'initial': None},
            '/Dc/0/Voltage': {'initial': None},
            '/Dc/0/Current': {'initial': None},
            '/Dc/0/Power': {'initial': None},
            '/Dc/0/Temperature': {'initial': 23.8},
            '/Info/BatteryLowVoltage': {'initial': None},
            '/Info/MaxChargeCurrent': {'initial': None},
            '/Info/MaxChargeVoltage': {'initial': None},
            '/Info/MaxDischargeCurrent': {'initial': None},
        },
        productname='ACME BMS battery',
        connection='CAN-bus')

    logger.info('Connected to dbus')


    # Track some items and reflect them
    conn = s._dbusservice._dbusconn
    track(conn, args.parent, '/Dc/0/Voltage',
        partial(_set_value, s, '/Dc/0/Voltage'))
    track(conn, args.parent, '/Dc/0/Current',
        partial(_set_value, s, '/Dc/0/Current'))
    track(conn, args.parent, '/Dc/0/Power',
        partial(_set_value, s, '/Dc/0/Power'))
    track(conn, args.parent, '/Soc',
        partial(_set_value, s, '/Soc'))

    logger.info('Switching over to gobject.MainLoop() (= event based)')
    mainloop = gobject.MainLoop()
    mainloop.run()

if __name__ == "__main__":
    main()
