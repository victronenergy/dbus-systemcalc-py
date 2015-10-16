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
import json

# Victron packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), './ext/velib_python'))
from vedbus import VeDbusService, VeDbusItemImport
from ve_utils import get_vrm_portal_id, exit_on_error
from dbusmonitor import DbusMonitor
from settingsdevice import SettingsDevice
from logger import setup_logging

softwareVersion = '1.16'

class SystemCalc:
	def __init__(self, dbusmonitor_gen=None, dbusservice_gen=None, settings_device_gen=None):
		self.STATE_IDLE = 0
		self.STATE_CHARGING = 1
		self.STATE_DISCHARGING = 2

		self.BATSERVICE_DEFAULT = 'default'
		self.BATSERVICE_NOBATTERY = 'nobattery'

		# Why this dummy? Because DbusMonitor expects these values to be there, even though we don't
		# need them. So just add some dummy data. This can go away when DbusMonitor is more generic.
		dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
		dbus_tree = {
			'com.victronenergy.solarcharger': {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/Dc/0/Voltage': dummy,
				'/Dc/0/Current': dummy},
			'com.victronenergy.pvinverter': {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/Ac/L1/Power': dummy,
				'/Ac/L2/Power': dummy,
				'/Ac/L3/Power': dummy,
				'/Position': dummy},
			'com.victronenergy.battery': {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/Dc/0/Voltage': dummy,
				'/Dc/0/Current': dummy,
				'/Dc/0/Power': dummy,
				'/Soc': dummy,
				'/TimeToGo': dummy,
				'/ConsumedAmphours': dummy},
			'com.victronenergy.vebus' : {
				'/Ac/ActiveIn/ActiveInput': dummy,
				'/Ac/ActiveIn/L1/P': dummy,
				'/Ac/ActiveIn/L2/P': dummy,
				'/Ac/ActiveIn/L3/P': dummy,
				'/Ac/Out/L1/P': dummy,
				'/Ac/Out/L2/P': dummy,
				'/Ac/Out/L3/P': dummy,
				'/Connected': dummy,
				'/Hub4/AcPowerSetpoint': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/State': dummy,
				'/Dc/0/Voltage': dummy,
				'/Dc/0/Current': dummy,
				'/Dc/0/Power': dummy,
				'/Soc': dummy},
			'com.victronenergy.charger': {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/Dc/0/Voltage': dummy,
				'/Dc/0/Current': dummy},
			'com.victronenergy.grid' : {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/ProductId' : dummy,
				'/DeviceType' : dummy,
				'/Ac/L1/Power': dummy,
				'/Ac/L2/Power': dummy,
				'/Ac/L3/Power': dummy},
			'com.victronenergy.genset' : {
				'/Connected': dummy,
				'/ProductName': dummy,
				'/Mgmt/Connection': dummy,
				'/ProductId' : dummy,
				'/DeviceType' : dummy,
				'/Ac/L1/Power': dummy,
				'/Ac/L2/Power': dummy,
				'/Ac/L3/Power': dummy},
			'com.victronenergy.settings' : {
				'/Settings/SystemSetup/AcInput1' : dummy,
				'/Settings/SystemSetup/AcInput2' : dummy}
		}

		if dbusmonitor_gen is None:
			self._dbusmonitor = DbusMonitor(dbus_tree, self._dbus_value_changed, self._device_added, self._device_removed)
		else:
			self._dbusmonitor = dbusmonitor_gen(dbus_tree)

		# Connect to localsettings
		supported_settings = {
			'batteryservice': ['/Settings/SystemSetup/BatteryService', self.BATSERVICE_DEFAULT, 0, 0],
			'hasdcsystem': ['/Settings/SystemSetup/HasDcSystem', 0, 0, 1],
			'writevebussoc': ['/Settings/SystemSetup/WriteVebusSoc', 0, 0, 1]}

		if settings_device_gen is None:
			self._settings = SettingsDevice(
				bus=dbus.SystemBus() if (platform.machine() == 'armv7l') else dbus.SessionBus(),
				supportedSettings=supported_settings,
				eventCallback=self._handlechangedsetting)
		else:
			self._settings = settings_device_gen(supported_settings, self._handlechangedsetting)

		# put ourselves on the dbus
		if dbusservice_gen is None:
			self._dbusservice = VeDbusService('com.victronenergy.system')
		else:
			self._dbusservice = dbusservice_gen('com.victronenergy.system')

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

		# At this moment, VRM portal ID is the MAC address of the CCGX. Anyhow, it should be string uniquely
		# identifying the CCGX.
		self._dbusservice.add_path('/Serial', value=get_vrm_portal_id())

		self._dbusservice.add_path(
			'/AvailableBatteryServices', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path(
			'/AvailableBatteryMeasurements', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path(
			'/AutoSelectedBatteryService', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path(
			'/AutoSelectedBatteryMeasurement', value=None, gettextcallback=self._gettext)
		self._dbusservice.add_path(
			'/ActiveBatteryService', value=None, gettextcallback=self._gettext)
		self._summeditems = {
			'/Ac/Grid/L1/Power': {'gettext': '%.0F W'},
			'/Ac/Grid/L2/Power': {'gettext': '%.0F W'},
			'/Ac/Grid/L3/Power': {'gettext': '%.0F W'},
			'/Ac/Grid/Total/Power': {'gettext': '%.0F W'},
			'/Ac/Grid/NumberOfPhases': {'gettext': '%.0F W'},
			'/Ac/Grid/ProductId': {'gettext': '%s'},
			'/Ac/Grid/DeviceType': {'gettext': '%s'},
			'/Ac/Genset/L1/Power': {'gettext': '%.0F W'},
			'/Ac/Genset/L2/Power': {'gettext': '%.0F W'},
			'/Ac/Genset/L3/Power': {'gettext': '%.0F W'},
			'/Ac/Genset/Total/Power': {'gettext': '%.0F W'},
			'/Ac/Genset/NumberOfPhases': {'gettext': '%.0F W'},
			'/Ac/Genset/ProductId': {'gettext': '%s'},
			'/Ac/Genset/DeviceType': {'gettext': '%s'},
			'/Ac/Consumption/L1/Power': {'gettext': '%.0F W'},
			'/Ac/Consumption/L2/Power': {'gettext': '%.0F W'},
			'/Ac/Consumption/L3/Power': {'gettext': '%.0F W'},
			'/Ac/Consumption/Total/Power': {'gettext': '%.0F W'},
			'/Ac/Consumption/NumberOfPhases': {'gettext': '%.0F W'},
			'/Ac/PvOnOutput/L1/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnOutput/L2/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnOutput/L3/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnOutput/Total/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnOutput/NumberOfPhases': {'gettext': '%.0F W'},
			'/Ac/PvOnGrid/L1/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGrid/L2/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGrid/L3/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGrid/Total/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGrid/NumberOfPhases': {'gettext': '%.0F W'},
			'/Ac/PvOnGenset/L1/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGenset/L2/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGenset/L3/Power': {'gettext': '%.0F W'},
			'/Ac/PvOnGenset/NumberOfPhases': {'gettext': '%d'},
			'/Ac/PvOnGenset/Total/Power': {'gettext': '%.0F W'},
			'/Dc/Pv/Power': {'gettext': '%.0F W'},
			'/Dc/Pv/Current': {'gettext': '%.1F A'},
			'/Dc/Battery/Voltage': {'gettext': '%.2F V'},
			'/Dc/Battery/Current': {'gettext': '%.1F A'},
			'/Dc/Battery/Power': {'gettext': '%.0F W'},
			'/Dc/Battery/Soc': {'gettext': '%.0F %%'},
			'/Dc/Battery/State': {'gettext': '%s'},
			'/Dc/Battery/TimeToGo': {'gettext': '%.0F s'},
			'/Dc/Battery/ConsumedAmphours': {'gettext': '%.1F Ah'},
			'/Dc/Charger/Power': {'gettext': '%.0F %%'},
			'/Dc/Vebus/Current': {'gettext': '%.1F A'},
			'/Dc/Vebus/Power': {'gettext': '%.0F W'},
			'/Dc/System/Power': {'gettext': '%.0F W'},
			'/Hub': {'gettext': '%s'},
			'/Ac/ActiveIn/Source': {'gettext': '%s'},
			'/VebusService': {'gettext': '%s'}
		}

		for path in self._summeditems.keys():
			self._dbusservice.add_path(path, value=None, gettextcallback=self._gettext)

		self._batteryservice = None
		self._determinebatteryservice()

		if self._batteryservice is None:
			logger.info("Battery service initialized to None (setting == %s)" %
				self._settings['batteryservice'])

		self._changed = True
		self._handleservicechange()
		for service, instance in self._dbusmonitor.get_service_list().items():
			path = self._get_service_mapping_path(service, instance)
			self._dbusservice.add_path(path, service)

		self._updatevalues()

		self._writeVebusSocCounter = 9
		gobject.timeout_add(1000, exit_on_error, self._handletimertick)

	def _handlechangedsetting(self, setting, oldvalue, newvalue):
		self._determinebatteryservice()
		self._changed = True

	def _determinebatteryservice(self):
		auto_battery_service = self._autoselect_battery_service()
		auto_battery_measurement = None
		if auto_battery_service is not None:
			services = self._dbusmonitor.get_service_list()
			if auto_battery_service in services:
				auto_battery_measurement = \
					self._get_instance_service_name(auto_battery_service, services[auto_battery_service])
				auto_battery_measurement = auto_battery_measurement.replace('.', '_').replace('/', '_') + '/Dc/0'
		self._dbusservice['/AutoSelectedBatteryMeasurement'] = auto_battery_measurement

		if self._settings['batteryservice'] == self.BATSERVICE_DEFAULT:
			newbatteryservice = auto_battery_service
			self._dbusservice['/AutoSelectedBatteryService'] = (
				'No battery monitor found' if newbatteryservice is None else
				self._get_readable_service_name(newbatteryservice))

		elif self._settings['batteryservice'] == self.BATSERVICE_NOBATTERY:
			self._dbusservice['/AutoSelectedBatteryService'] = None
			newbatteryservice = None

		else:
			self._dbusservice['/AutoSelectedBatteryService'] = None

			s = self._settings['batteryservice'].split('/')
			logger.error("The battery setting (%s) is invalid!" % self._settings['batteryservice'])
			serviceclass = s[0]
			instance = int(s[1]) if len(s) == 2 else None
			services = self._dbusmonitor.get_service_list(classfilter=serviceclass)
			if instance not in services.values():
				# Once chosen battery monitor does not exist. Don't auto change the setting (it might come
				# back). And also don't autoselect another.
				newbatteryservice = None
			else:
				# According to https://www.python.org/dev/peps/pep-3106/, dict.keys() and dict.values()
				# always have the same order.
				newbatteryservice = services.keys()[services.values().index(instance)]

		if newbatteryservice != self._batteryservice:
			services = self._dbusmonitor.get_service_list()
			instance = services.get(newbatteryservice, None)
			if instance is None:
				battery_service = None
			else:
				battery_service = self._get_instance_service_name(newbatteryservice, instance)
			self._dbusservice['/ActiveBatteryService'] = battery_service
			logger.info("Battery service, setting == %s, changed from %s to %s (%s)" %
				(self._settings['batteryservice'], self._batteryservice, newbatteryservice, instance))
			self._batteryservice = newbatteryservice

	def _autoselect_battery_service(self):
		# Default setting business logic:
		# first try to use a battery service (BMV or Lynx Shunt VE.Can). If there
		# is more than one battery service, just use a random one. If no battery service is
		# available, check if there are not Solar chargers and no normal chargers. If they are not
		# there, assume this is a hub-2, hub-3 or hub-4 system and use VE.Bus SOC.
		batteries = self._get_connected_service_list('com.victronenergy.battery')

		if len(batteries) > 0:
			return sorted(batteries)[0]  # Pick a random battery service

		if self._get_first_connected_service('com.victronenergy.solarcharger') is not None:
			return None

		if self._get_first_connected_service('com.victronenergy.charger') is not None:
			return None

		vebus_services = self._get_first_connected_service('com.victronenergy.vebus')
		if vebus_services is None:
			return None
		return vebus_services[0]

	# Called on a one second timer
	def _handletimertick(self):
		if self._changed:
			self._updatevalues()
		self._changed = False

		self._writeVebusSocCounter += 1
		if self._writeVebusSocCounter >= 10:
			self._writeVebusSoc()
			self._writeVebusSocCounter = 0

		return True  # keep timer running

	def _writeVebusSoc(self):
		# ==== COPY BATTERY SOC TO VEBUS ====
		if self._settings['writevebussoc'] and self._dbusservice['/VebusService'] and self._dbusservice['/Dc/Battery/Soc'] and \
			self._batteryservice.split('.')[2] != 'vebus':

			logger.debug("writing this soc to vebus: %d", self._dbusservice['/Dc/Battery/Soc'])
			self._dbusmonitor.get_item(self._dbusservice['/VebusService'], '/Soc').set_value(self._dbusservice['/Dc/Battery/Soc'])

	def _updatevalues(self):
		# ==== PREPARATIONS ====
		# Determine values used in logic below
		vebusses = self._dbusmonitor.get_service_list('com.victronenergy.vebus')
		vebuspower = 0
		for vebus in vebusses:
			v = self._dbusmonitor.get_value(vebus, '/Dc/0/Voltage')
			i = self._dbusmonitor.get_value(vebus, '/Dc/0/Current')
			if v is not None and i is not None:
				vebuspower += v * i

		# ==== PVINVERTERS ====
		pvinverters = self._dbusmonitor.get_service_list('com.victronenergy.pvinverter')
		newvalues = {}
		pos = {0: '/Ac/PvOnGrid', 1: '/Ac/PvOnOutput', 2: '/Ac/PvOnGenset'}
		total = {0: None, 1: None, 2: None}
		for pvinverter in pvinverters:
			# Position will be None if PV inverter service has just been removed (after retrieving the
			# service list).
			position = pos.get(self._dbusmonitor.get_value(pvinverter, '/Position'))
			if position is not None:
				for phase in range(1, 4):
					power = self._dbusmonitor.get_value(pvinverter, '/Ac/L%s/Power' % phase)
					if power is not None:
						path = '%s/L%s/Power' % (position, phase)
						newvalues[path] = _safeadd(newvalues.get(path), power)

		for path in pos.values():
			self._compute_phase_totals(path, newvalues)

		# ==== SOLARCHARGERS ====
		solarchargers = self._dbusmonitor.get_service_list('com.victronenergy.solarcharger')
		solarcharger_batteryvoltage = None
		for solarcharger in solarchargers:
			v = self._dbusmonitor.get_value(solarcharger, '/Dc/0/Voltage')
			if v is None:
				continue
			i = self._dbusmonitor.get_value(solarcharger, '/Dc/0/Current')
			if i is None:
				continue

			if '/Dc/Pv/Power' not in newvalues:
				newvalues['/Dc/Pv/Power'] = v * i
				newvalues['/Dc/Pv/Current'] = i
				solarcharger_batteryvoltage = v
			else:
				newvalues['/Dc/Pv/Power'] += v * i
				newvalues['/Dc/Pv/Current'] += i

		# ==== CHARGERS ====
		chargers = self._dbusmonitor.get_service_list('com.victronenergy.charger')
		charger_batteryvoltage = None
		for charger in chargers:
			# Assume the battery connected to output 0 is the main battery
			v = self._dbusmonitor.get_value(charger, '/Dc/0/Voltage')
			if v is None:
				continue

			charger_batteryvoltage = v

			i = self._dbusmonitor.get_value(charger, '/Dc/0/Current')
			if i is None:
				continue

			if '/Dc/Charger/Power' not in newvalues:
				newvalues['/Dc/Charger/Power'] = v * i
			else:
				newvalues['/Dc/Charger/Power'] += v * i

		# ==== BATTERY ====
		if self._batteryservice is not None:
			batteryservicetype = self._batteryservice.split('.')[2]  # either 'battery' or 'vebus'
			newvalues['/Dc/Battery/Soc'] = self._dbusmonitor.get_value(self._batteryservice,'/Soc')
			newvalues['/Dc/Battery/TimeToGo'] = self._dbusmonitor.get_value(self._batteryservice,'/TimeToGo')
			newvalues['/Dc/Battery/ConsumedAmphours'] = self._dbusmonitor.get_value(self._batteryservice,'/ConsumedAmphours')

			if batteryservicetype == 'battery':
				newvalues['/Dc/Battery/Voltage'] = self._dbusmonitor.get_value(self._batteryservice, '/Dc/0/Voltage')
				newvalues['/Dc/Battery/Current'] = self._dbusmonitor.get_value(self._batteryservice, '/Dc/0/Current')
				newvalues['/Dc/Battery/Power'] = self._dbusmonitor.get_value(self._batteryservice, '/Dc/0/Power')

			elif batteryservicetype == 'vebus':
				newvalues['/Dc/Battery/Voltage'] = self._dbusmonitor.get_value(self._batteryservice, '/Dc/0/Voltage')
				newvalues['/Dc/Battery/Current'] = self._dbusmonitor.get_value(self._batteryservice, '/Dc/0/Current')
				if newvalues['/Dc/Battery/Voltage'] is not None and newvalues['/Dc/Battery/Current'] is not None:
					newvalues['/Dc/Battery/Power'] = (
						newvalues['/Dc/Battery/Voltage'] * newvalues['/Dc/Battery/Current'])

			p = newvalues.get('/Dc/Battery/Power', None)
			if p is not None:
				if p > 30:
					newvalues['/Dc/Battery/State'] = self.STATE_CHARGING
				elif p < -30:
					newvalues['/Dc/Battery/State'] = self.STATE_DISCHARGING
				else:
					newvalues['/Dc/Battery/State'] = self.STATE_IDLE

		else:
			batteryservicetype = None
			if solarcharger_batteryvoltage is not None:
				newvalues['/Dc/Battery/Voltage'] = solarcharger_batteryvoltage
			elif charger_batteryvoltage is not None:
				newvalues['/Dc/Battery/Voltage'] = charger_batteryvoltage
			else:
				# CCGX-connected system consists of only a Multi, but it is not user-selected, nor
				# auto-selected as the battery-monitor, probably because there are other loads or chargers.
				# In that case, at least use its reported battery voltage.
				vebusses = self._dbusmonitor.get_service_list('com.victronenergy.vebus')
				for vebus in vebusses:
					v = self._dbusmonitor.get_value(vebus, '/Dc/0/Voltage')
					if v is not None:
						newvalues['/Dc/Battery/Voltage'] = v

			if self._settings['hasdcsystem'] == 0 and '/Dc/Battery/Voltage' in newvalues:
				# No unmonitored DC loads or chargers, and also no battery monitor: derive battery watts
				# and amps from vebus, solarchargers and chargers.
				assert '/Dc/Battery/Power' not in newvalues
				assert '/Dc/Battery/Current' not in newvalues
				p = newvalues.get('/Dc/Pv/Power', 0) + newvalues.get('/Dc/Charger/Power', 0) + vebuspower
				voltage = newvalues['/Dc/Battery/Voltage']
				newvalues['/Dc/Battery/Current'] = p / voltage if voltage > 0 else None
				newvalues['/Dc/Battery/Power'] = p

		# ==== SYSTEM ====
		if self._settings['hasdcsystem'] == 1 and batteryservicetype == 'battery':
			# Calculate power being generated/consumed by not measured devices in the network.
			# /Dc/System: positive: consuming power
			# VE.Bus: Positive: current flowing from the Multi to the dc system or battery
			# Solarcharger & other chargers: positive: charging
			# battery: Positive: charging battery.
			# battery = solarcharger + charger + ve.bus - system

			battery_power = newvalues.get('/Dc/Battery/Power')
			if battery_power is not None:
				dc_pv_power = newvalues.get('/Dc/Pv/Power', 0)
				charger_power = newvalues.get('/Dc/Charger/Power', 0)
				newvalues['/Dc/System/Power'] = dc_pv_power + charger_power + vebuspower - battery_power

		# ==== Vebus ====
		# Assume there's only 1 multi service present on the D-Bus
		multi = self._get_first_connected_service('com.victronenergy.vebus')
		multi_path = None
		if multi is not None:
			multi_path = multi[0]
			dc_current = self._dbusmonitor.get_value(multi_path, '/Dc/0/Current')
			newvalues['/Dc/Vebus/Current'] = dc_current
			dc_power = self._dbusmonitor.get_value(multi_path, '/Dc/0/Power')
			# Just in case /Dc/0/Power is not available
			if dc_power == None and dc_current is not None:
				dc_voltage = self._dbusmonitor.get_value(multi_path, '/Dc/0/Voltage')
				if dc_voltage is not None:
					dc_power = dc_voltage * dc_current
			# Note that there is also vebuspower, which is the total DC power summed over all multis.
			# However, this value cannot be combined with /Dc/Multi/Current, because it does not make sense
			# to add the Dc currents of all multis if they do not share the same DC voltage.
			newvalues['/Dc/Vebus/Power'] = dc_power

		newvalues['/VebusService'] = multi_path

		# ===== AC IN SOURCE =====
		ac_in_source = None
		active_input = self._dbusmonitor.get_value(multi_path, '/Ac/ActiveIn/ActiveInput')
		if active_input is not None:
			settings_path = '/Settings/SystemSetup/AcInput%s' % (active_input + 1)
			ac_in_source = self._dbusmonitor.get_value('com.victronenergy.settings', settings_path)
		newvalues['/Ac/ActiveIn/Source'] = ac_in_source

		# ===== HUB MODE =====
		# The code below should be executed after PV inverter data has been updated, because we need the
		# PV inverter total power to update the consumption.
		hub = None
		if self._dbusmonitor.get_value(multi_path, '/Hub4/AcPowerSetpoint') is not None:
			hub = 4
		elif newvalues.get('/Dc/Pv/Power', None) is not None:
			hub = 1
		elif newvalues.get('/Ac/PvOnOutput/Total/Power', None) is not None:
			hub = 2
		elif newvalues.get('/Ac/PvOnGrid/Total/Power', None) is not None or \
			newvalues.get('/Ac/PvOnGenset/Total/Power', None) is not None:
			hub = 3
		newvalues['/Hub'] = hub

		# ===== GRID METERS & CONSUMPTION ====
		consumption = { "L1" : None, "L2" : None, "L3" : None }
		for device_type in ['Grid', 'Genset']:
			servicename = 'com.victronenergy.%s' % device_type.lower()
			energy_meter = self._get_first_connected_service(servicename)
			em_service = None if energy_meter is None else energy_meter[0]
			uses_active_input = False
			if multi_path is not None:
				# If a grid meter is present we use values from it. If not, we look at the multi. If it has
				# AcIn1 or AcIn2 connected to the grid, we use those values.
				# com.victronenergy.grid.??? indicates presence of an energy meter used as grid meter.
				# com.victronenergy.vebus.???/Ac/ActiveIn/ActiveInput: decides which whether we look at AcIn1
				# or AcIn2 as possible grid connection.
				if ac_in_source is not None:
					uses_active_input = ac_in_source > 0 and (ac_in_source == 2) == (device_type == 'Genset')
			for phase in consumption:
				p = None
				pvpower = newvalues.get('/Ac/PvOn%s/%s/Power' % (device_type, phase))
				if em_service is not None:
					p = self._dbusmonitor.get_value(em_service, '/Ac/%s/Power' % phase)
					# Compute consumption between energy meter and multi (meter power - multi AC in) and
					# add an optional PV inverter on input to the mix.
					c = consumption[phase]
					if uses_active_input:
						ac_in = self._dbusmonitor.get_value(multi_path, '/Ac/ActiveIn/%s/P' % phase)
						if ac_in is not None:
							c = _safeadd(c, -ac_in)
					# If there's any power coming from a PV inverter in the inactive AC in (which is unlikely),
					# it will still be used, because there may also be a load in the same ACIn consuming
					# power, or the power could be fed back to the net.
					c = _safeadd(c, p, pvpower)
					consumption[phase] = None if c is None else max(0, c)
				else:
					if uses_active_input:
						p = self._dbusmonitor.get_value(multi_path, '/Ac/ActiveIn/%s/P' % phase)
					# No relevant energy meter present. Assume there is no load between the grid and the multi.
					# There may be a PV inverter present though (Hub-3 setup).
					if pvpower != None:
						p = _safeadd(p, -pvpower)
				newvalues['/Ac/%s/%s/Power' % (device_type, phase)] = p
			self._compute_phase_totals('/Ac/%s' % device_type, newvalues)
			if em_service is not None:
				newvalues['/Ac/%s/ProductId' % device_type] = self._dbusmonitor.get_value(em_service, '/ProductId')
				newvalues['/Ac/%s/DeviceType' % device_type] = self._dbusmonitor.get_value(em_service, '/DeviceType')
		for phase in consumption:
			c = consumption[phase]
			pvpower = newvalues.get('/Ac/PvOnOutput/%s/Power' % phase)
			c = _safeadd(c, pvpower)
			if multi_path is not None:
				ac_out = self._dbusmonitor.get_value(multi_path, '/Ac/Out/%s/P' % phase)
				c = _safeadd(c, ac_out)
			newvalues['/Ac/Consumption/%s/Power' % phase] = None if c is None else max(0, c)
		self._compute_phase_totals('/Ac/Consumption', newvalues)
		# TODO EV Add Multi DeviceType & ProductID. Unfortunately, the com.victronenergy.vebus.??? tree does
		# not contain a /ProductId entry.

		# ==== UPDATE DBUS ITEMS ====
		for path in self._summeditems.keys():
			# Why the None? Because we want to invalidate things we don't have anymore.
			self._dbusservice[path] = newvalues.get(path, None)

	def _handleservicechange(self):
		# Update the available battery monitor services, used to populate the dropdown in the settings.
		# Below code makes a dictionary. The key is [dbuserviceclass]/[deviceinstance]. For example
		# "battery/245". The value is the name to show to the user in the dropdown. The full dbus-
		# servicename, ie 'com.victronenergy.vebus.ttyO1' is not used, since the last part of that is not
		# fixed. dbus-serviceclass name and the device instance are already fixed, so best to use those.

		services = self._get_connected_service_list('com.victronenergy.vebus')
		services.update(self._get_connected_service_list('com.victronenergy.battery'))

		ul = {self.BATSERVICE_DEFAULT: 'Automatic', self.BATSERVICE_NOBATTERY: 'No battery monitor'}
		for servicename, instance in services.items():
			key = self._get_instance_service_name(servicename, instance)
			ul[key] = self._get_readable_service_name(servicename)
		self._dbusservice['/AvailableBatteryServices'] = json.dumps(ul)

		ul = {self.BATSERVICE_DEFAULT: 'Automatic', self.BATSERVICE_NOBATTERY: 'No battery monitor'}
		# For later: for device supporting multiple Dc measurement we should add entries for /Dc/1 etc as
		# well.
		for servicename, instance in services.items():
			key = self._get_instance_service_name(servicename, instance).replace('.', '_').replace('/', '_') + '/Dc/0'
			ul[key] = self._get_readable_service_name(servicename)
		self._dbusservice['/AvailableBatteryMeasurements'] = dbus.Dictionary(ul, signature='sv')

		self._determinebatteryservice()

		self._changed = True

	def _get_readable_service_name(self, servicename):
		return (self._dbusmonitor.get_value(servicename, '/ProductName') + ' on ' +
						self._dbusmonitor.get_value(servicename, '/Mgmt/Connection'))

	def _get_instance_service_name(self, service, instance):
		return '%s/%s' % ('.'.join(service.split('.')[0:3]), instance)

	def _get_service_mapping_path(self, service, instance):
		sn = self._get_instance_service_name(service, instance).replace('.', '_').replace('/', '_')
		return '/ServiceMapping/%s' % sn

	def _remove_unconnected_services(self, services):
		# Workaround: because com.victronenergy.vebus is available even when there is no vebus product
		# connected. Remove any that is not connected. For this, we use /State since mandatory path
		# /Connected is not implemented in mk2dbus.
		for servicename in services.keys():
			if ((servicename.split('.')[2] == 'vebus' and self._dbusmonitor.get_value(servicename, '/State') is None)
				or self._dbusmonitor.get_value(servicename, '/Connected') != 1
				or self._dbusmonitor.get_value(servicename, '/ProductName') is None
				or self._dbusmonitor.get_value(servicename, '/Mgmt/Connection') is None):
				del services[servicename]

	def _dbus_value_changed(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
		self._changed = True

		# Workaround because com.victronenergy.vebus is available even when there is no vebus product
		# connected.
		if (dbusPath in ['/Connected', '/ProductName', '/Mgmt/Connection'] or
			(dbusPath == '/State' and dbusServiceName.split('.')[0:3] == ['com', 'victronenergy', 'vebus'])):
			self._handleservicechange()

	def _device_added(self, service, instance):
		path = self._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			self._dbusservice[path] = service
		else:
			self._dbusservice.add_path(path, service)
		self._handleservicechange()

	def _device_removed(self, service, instance):
		path = self._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			del self._dbusservice[path]
		self._handleservicechange()

	def _gettext(self, path, value):
		if path == '/Dc/Battery/State':
			state = {self.STATE_IDLE: 'Idle', self.STATE_CHARGING: 'Charging',
				self.STATE_DISCHARGING: 'Discharging'}
			return state[value]
		item = self._summeditems.get(path)
		if item is not None:
			return item['gettext'] % value
		return value

	def _compute_phase_totals(self, path, newvalues):
		total_power = None
		number_of_phases = None
		for phase in range(1, 4):
			p = newvalues.get('%s/L%s/Power' % (path, phase))
			total_power = _safeadd(total_power, p)
			if p is not None:
				number_of_phases = phase
		newvalues[path + '/Total/Power'] = total_power
		newvalues[path + '/NumberOfPhases'] = number_of_phases

	def _get_connected_service_list(self, classfilter=None):
		services = self._dbusmonitor.get_service_list(classfilter=classfilter)
		self._remove_unconnected_services(services)
		return services

	def _get_first_connected_service(self, classfilter=None):
		services = self._get_connected_service_list(classfilter=classfilter)
		if len(services) == 0:
			return None
		return services.items()[0]

def _safeadd(*values):
	'''Adds all parameters passed to this function. Parameters which are None are ignored. If all parameters
	are None, the function will return None as well.'''
	r = None
	for v in values:
		if v is not None:
			if r is None:
				r = v
			else:
				r += v
	return r

if __name__ == "__main__":
	# Argument parsing
	parser = argparse.ArgumentParser(
		description='Converts readings from AC-Sensors connected to a VE.Bus device in a pvinverter ' +
					'D-Bus service.'
	)

	parser.add_argument("-d", "--debug", help="set logging level to debug",
					action="store_true")

	args = parser.parse_args()

	print("-------- dbus_systemcalc, v" + softwareVersion + " is starting up --------")
	logger = setup_logging(args.debug)

	# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
	DBusGMainLoop(set_as_default=True)

	systemcalc = SystemCalc()

	# Start and run the mainloop
	logger.info("Starting mainloop, responding only on events")
	mainloop = gobject.MainLoop()
	mainloop.run()
