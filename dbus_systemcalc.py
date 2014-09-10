#!/usr/bin/python -u
# -*- coding: utf-8 -*-

from dbus.mainloop.glib import DBusGMainLoop
import gobject
from gobject import idle_add
import dbus
import dbus.service
import inspect
import platform
import logging
import argparse
import sys
import os

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), './ext/velib_python'))
from vedbus import VeDbusService, VeDbusItemImport
from dbusmonitor import DbusMonitor

softwareVersion = '1.00'

class SystemCalc:
	def __init__(self):
		# Why this dummy? DbusMonitor expects these values to be there, even though we don
		# need them. So just add some dummy data. This can go away when DbusMonitor is more generic.
		dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}

		self._dbusmonitor = DbusMonitor({
			'com.victronenergy.solarcharger': {
				'/Dc/V': dummy,
				'/Dc/I': dummy},
			'com.victronenergy.pvinverter': {
				'/Ac/L1/Power': dummy,
				'/Ac/L2/Power': dummy,
				'/Ac/L3/Power': dummy,
				'/Position': dummy}
		}, self._dbus_value_changed, self._device_added, self._device_removed)

		# put ourselves on the dbus

		self._dbusservice = VeDbusService('com.victronenergy.kwhcounters')  #'com.victronenergy.system.calc')
		self._dbusservice.add_mandatory_paths(
			processname=__file__,
			processversion=softwareVersion,
			connection='data from other dbus processes',
			deviceinstance=0,
			productid=None,
			productname=None,
			firmwareversion=None,
			hardwareversion=None,
			connected=1)

		"""
		self._dbusservice.add_path('/Battery/Voltage', value=None, gettextcallback=self._gettext,
			description='Battery voltage')
		self._dbusservice.add_path('/Battery/Current', value=None, gettextcallback=self._gettext,
			description='Battery current')
		self._dbusservice.add_path('/Vebus/ChargeCurrent', value=None, gettextcallback=self._gettext,
			description='VE.Bus charge current')
		self._dbusservice.add_path('/Battery/Soc', value=None, gettextcallback=self._gettext,
			description='State of charge')
		self._dbusservice.add_path('/Battery/State', value=None, gettextcallback=self._gettext,
			description='Battery state (idle, charging, discharging)')
		self._dbusservice.add_path('/Battery/ConsumedAh', value=None, gettextcallback=self._gettext,
			description='Battery consumed Ah')
		self._dbusservice.add_path('/Battery/TimeToGo', value=None, gettextcallback=self._gettext,
			description='Battery time to go')
		"""


		self._summeditems = {		
			'/Ac/PvOnOutput/L1/Power': None,
			'/Ac/PvOnOutput/L2/Power': None,
			'/Ac/PvOnOutput/L3/Power': None,
			'/Ac/PvOnOutput/Total/Power': None,
			'/Ac/PvOnGrid/L1/Power': None,
			'/Ac/PvOnGrid/L2/Power': None,
			'/Ac/PvOnGrid/L3/Power': None,
			'/Ac/PvOnGrid/Total/Power': None,
			'/Ac/PvOnGenset/L1/Power': None,
			'/Ac/PvOnGenset/L2/Power': None,
			'/Ac/PvOnGenset/L3/Power': None,
			'/Ac/PvOnGenset/Total/Power': None,
			'/Dc/Pv/Power': None}
			# TODO: Change D-Bus service name back to com.victronenergy.system

		

		for path in self._summeditems.keys():
			self._dbusservice.add_path(path, value=None, gettextcallback=self._gettext)

		"""
		self._dbusservice.add_path('/Ac/Consumption/L1/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Consumption/L2/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Consumption/L3/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Grid/L1/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Grid/L2/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Grid/L3/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Genset/L1/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Genset/L2/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Ac/Genset/L3/Power', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path('/Dc/System', value=None, gettextcallback=self._gettext)
		"""

		self._changed = True
		self._updatevalues()
		gobject.timeout_add(2000, self._updatevalues)

	def _updatevalues(self):
		if not self._changed:
			logging.debug('Nothing changed, skipping')
			return True

		# ==== PVINVERTERS ====
		pvinverters = self._dbusmonitor.get_service_list('com.victronenergy.pvinverter')
		newvalues = {}
		phases = ['1', '2', '3']
		pos = {0: '/Ac/PvOnGrid/', 1: '/Ac/PvOnOutput/', 2: '/Ac/PvOnGenset/'}
		total = {0: 0, 1: 0, 2: 0}
		for pvinverter in pvinverters:
			position = self._dbusmonitor.get_value(pvinverter, '/Position')
			# Only work with pvinverters on the output for now.
			# TODO: work with all

			for phase in phases:
				power = self._dbusmonitor.get_value(pvinverter, '/Ac/L' + phase + '/Power')
				if power is None:
					continue

				path = pos[position] + 'L' + str(int(phase)) + '/Power'
				if path not in newvalues:
					newvalues[path] = power
				else:
					newvalues[path] += power
				print str(path) + " - " + str(power)
				total[position] += power

		newvalues['/Ac/PvOnGrid/Total/Power'] = total[0]
		newvalues['/Ac/PvOnOutput/Total/Power'] = total[1]
		newvalues['/Ac/PvOnGenset/Total/Power'] = total[2]

		# ==== SOLARCHARGERS ====
		solarchargers = self._dbusmonitor.get_service_list('com.victronenergy.solarcharger')
		for solarcharger in solarchargers:
			v = self._dbusmonitor.get_value(solarcharger, '/Dc/V')
			if v is None:
				continue
			i = self._dbusmonitor.get_value(solarcharger, '/Dc/I')
			if i is None:
				continue

			if '/Dc/Pv/Power' not in newvalues:
				newvalues['/Dc/Pv/Power'] = v * i
			else:
				newvalues['/Dc/Pv/Power'] += v * i

		# ==== UPDATE DBUS ITEMS ====
		for path in self._summeditems.keys():
			self._dbusservice[path] = newvalues[path] if path in newvalues else None

		self._changed = False
		logging.debug("New values: %s" % newvalues)

		return True  # Keep timer running

	def _dbus_value_changed(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
		self._changed = True

	def _device_added(self, service, instance):
		pass

	def _device_removed(self, service, instance):
		pass

	def _gettext(self, path, value):
		return "TODO: implement gettext"


if __name__ == "__main__":
	# Argument parsing
	parser = argparse.ArgumentParser(
		description='Converts readings from AC-Sensors connected to a VE.Bus device in a pvinverter ' +
					'D-Bus service.'
	)

	parser.add_argument("-d", "--debug", help="set logging level to debug",
					action="store_true")

	args = parser.parse_args()

	# Init logging
	logging.basicConfig(level=(logging.DEBUG if args.debug else logging.INFO))
	logging.info("-------- dbus_systemcalc, v" + softwareVersion + " is starting up --------")
	logLevel = {0: 'NOTSET', 10: 'DEBUG', 20: 'INFO', 30: 'WARNING', 40: 'ERROR'}
	logging.info('Loglevel set to ' + logLevel[logging.getLogger().getEffectiveLevel()])

	# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
	DBusGMainLoop(set_as_default=True)

	systemcalc = SystemCalc()

	# Start and run the mainloop
	logging.info("Starting mainloop, responding only on events")
	mainloop = gobject.MainLoop()
	mainloop.run()
