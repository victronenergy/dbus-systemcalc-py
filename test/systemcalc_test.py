#!/usr/bin/env python
import json
import logging
import math
import os
import sys
import tempfile
import unittest

# our own packages
test_dir = os.path.dirname(__file__)
sys.path.insert(0, test_dir)
sys.path.insert(1, os.path.join(test_dir, '..', 'ext', 'velib_python', 'test'))
sys.path.insert(1, os.path.join(test_dir, '..'))
import dbus_systemcalc
import delegates
import mock_gobject
from mock_dbus_monitor import MockDbusMonitor
from mock_dbus_service import MockDbusService
from mock_settings_device import MockSettingsDevice


# Override the logging set in dbus_systemcalc, now only warnings and errors will be logged. This reduces the
# output of the unit test to a few lines.
dbus_systemcalc.logger = logging.getLogger()
# Patch an alternative function to get the portal ID, because the original retrieves the ID by getting
# the MAC address of 'eth0' which may not be available.
dbus_systemcalc.get_vrm_portal_id = lambda: 'aabbccddeeff'
mock_gobject.patch_gobject(dbus_systemcalc.gobject)


class MockSystemCalc(dbus_systemcalc.SystemCalc):
	def _create_dbus_monitor(self, *args, **kwargs):
		return MockDbusMonitor(*args, **kwargs)

	def _create_settings(self, *args, **kwargs):
		return MockSettingsDevice(*args, **kwargs)

	def _create_dbus_service(self):
		return MockDbusService('com.victronenergy.system')


class TestSystemCalcBase(unittest.TestCase):
	def __init__(self, methodName='runTest'):
		unittest.TestCase.__init__(self, methodName)

	def setUp(self):
		mock_gobject.timer_manager.reset()
		self._system_calc = MockSystemCalc()
		self._monitor = self._system_calc._dbusmonitor
		self._service = self._system_calc._dbusservice

	def _update_values(self, interval=1000):
		mock_gobject.timer_manager.run(interval)

	def _add_device(self, service, values, connected=True, product_name='dummy', connection='dummy'):
		values['/Connected'] = 1 if connected else 0
		values['/ProductName'] = product_name
		values['/Mgmt/Connection'] = connection
		values.setdefault('/DeviceInstance', 0)
		self._monitor.add_service(service, values)

	def _remove_device(self, service):
		self._monitor.remove_service(service)

	def _set_setting(self, path, value):
		self._system_calc._settings[self._system_calc._settings.get_short_name(path)] = value

	def _check_values(self, values):
		ok = True
		for k, v in values.items():
			v2 = self._service[k] if k in self._service else None
			if isinstance(v, (int, float)) and v2 is not None:
				d = abs(v - v2)
				if d > 1e-6:
					ok = False
					break
			else:
				if v != v2:
					ok = False
					break
		if ok:
			return
		msg = ''
		for k, v in values.items():
			msg += '{0}:\t{1}'.format(k, v)
			if k in self._service:
				msg += '\t{}'.format(self._service[k])
			msg += '\n'
		self.assertTrue(ok, msg)

	def _check_external_values(self, values):
		"""Checks a list of values from external (ie. not com.victronenergy.system) services.
		Example for values:
		['com.victronenergy.vebus.ttyO1', { '/State': 3, '/Mode': 7 },
		'com.victronenergy.hub4', { '/MaxChargePower' : 342 }]
		"""
		ok = True
		for service, objects in values.items():
			for path, value in objects.items():
				v = self._monitor.get_value(service, path)
				if isinstance(value, (int, float)) and v is not None:
					d = abs(value - v)
					if d > 1e-6:
						ok = False
						break
				else:
					if v != value:
						ok = False
						break
		if ok:
			return
		msg = ''
		for service, objects in values.items():
			for path, value in objects.items():
				msg += '{0}:\t{1}'.format(path, value)
				v = self._monitor.get_value(service, path)
				msg += '\t{}'.format(v)
				msg += '\n'
		self.assertTrue(ok, msg)


class TestSystemCalc(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='Multi',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/DeviceInstance': 0,
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),  # Hub-4 assistant
				'/Dc/0/MaxChargeCurrent': None,
				'/Soc': 53.2,
				'/State': 3,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

	def test_ac_in_grid(self):
		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/L2/Power': None,
			'/Ac/Grid/L3/Power': None,
			'/Ac/Genset/NumberOfPhases': None,
			'/Ac/Consumption/L1/Power': 100,
			'/Ac/Consumption/L2/Power': None,
			'/Ac/Consumption/L3/Power': None,
			'/Ac/ConsumptionOnOutput/L1/Power': 100,
			'/Ac/ConsumptionOnOutput/L2/Power': None,
			'/Ac/ConsumptionOnOutput/L3/Power': None,
			'/Ac/ConsumptionOnInput/L1/Power': 0,
			'/Ac/ConsumptionOnInput/L2/Power': None,
			'/Ac/ConsumptionOnInput/L3/Power': None
		})

	def test_ac_in_genset(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 2,
			'/Ac/Genset/L1/Power': 123,
			'/Ac/Grid/L1/Power': None
		})

	def test_ac_in_not_available(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 0)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 0,
			'/Ac/Grid/NumberOfPhases': None,
			'/Ac/Genset/NumberOfPhases': None
		})

	def test_ac_in_shore(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 3)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 3,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Genset/NumberOfPhases': None
		})

	def test_ac_in_grid_3p(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', 100)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', 150)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', 200)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', 80)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', 90)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', 100)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/L1/Power': 100,
			'/Ac/Grid/L2/Power': 150,
			'/Ac/Grid/L3/Power': 200,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Genset/L1/Power': None,
			'/Ac/Genset/NumberOfPhases': None,
			'/Ac/Consumption/L1/Power': 80,
			'/Ac/Consumption/L2/Power': 90,
			'/Ac/Consumption/L3/Power': 100,
			'/Ac/ConsumptionOnOutput/L1/Power': 80,
			'/Ac/ConsumptionOnOutput/L2/Power': 90,
			'/Ac/ConsumptionOnOutput/L3/Power': 100,
			'/Ac/ConsumptionOnInput/L1/Power': 0,
			'/Ac/ConsumptionOnInput/L2/Power': 0,
			'/Ac/ConsumptionOnInput/L3/Power': 0
		})

	def test_ac_gridmeter(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', {'/Ac/L1/Power': 1230})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 100 + 500,
			'/Ac/ConsumptionOnOutput/L1/Power': 100,
			'/Ac/ConsumptionOnInput/L1/Power': 1230 - 123 + 500
		})

	def test_ac_gridmeter_3p(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', {
			'/Ac/L1/Power': 1230,
			'/Ac/L2/Power': 1130,
			'/Ac/L3/Power': 1030})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Ac/L2/Power': 400,
			'/Ac/L3/Power': 200,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/L2/Power': 1130,
			'/Ac/Grid/L3/Power': 1030,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 100 + 500,
			'/Ac/Consumption/L2/Power': 1130 + 400,
			'/Ac/Consumption/L3/Power': 1030 + 200,
			'/Ac/ConsumptionOnInput/L1/Power': 1230 - 123 + 500,
			'/Ac/ConsumptionOnInput/L2/Power': 1130 + 400,
			'/Ac/ConsumptionOnInput/L3/Power': 1030 + 200,
			'/Ac/ConsumptionOnOutput/L1/Power': 100,
			# It's one phase on output
			'/Ac/ConsumptionOnOutput/NumberOfPhases': 1,
			'/Ac/ConsumptionOnOutput/L2/Power': None,
			'/Ac/ConsumptionOnOutput/L3/Power': None

		})

	def test_ac_gridmeter_3p_ignore_acout(self):
		self._set_setting('/Settings/SystemSetup/HasAcOutSystem', 0)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', 20)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', -10)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', 30)
		self._add_device('com.victronenergy.grid.ttyUSB1', {
			'/Ac/L1/Power': 1230,
			'/Ac/L2/Power': 1130,
			'/Ac/L3/Power': 1030})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Ac/L2/Power': 400,
			'/Ac/L3/Power': 200,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/L2/Power': 1130,
			'/Ac/Grid/L3/Power': 1030,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 500,
			'/Ac/Consumption/L2/Power': 1130 + 400,
			'/Ac/Consumption/L3/Power': 1030 + 200,
			'/Ac/ConsumptionOnInput/L1/Power': 1230 - 123 + 500,
			'/Ac/ConsumptionOnInput/L2/Power': 1130 + 400,
			'/Ac/ConsumptionOnInput/L3/Power': 1030 + 200,
			'/Ac/ConsumptionOnOutput/NumberOfPhases': None,
			'/Ac/ConsumptionOnOutput/L1/Power': None,
			'/Ac/ConsumptionOnOutput/L2/Power': None,
			'/Ac/ConsumptionOnOutput/L3/Power': None

		})

	def test_ac_gridmeter_3p_has_acout_notset(self):
		self._set_setting('/Settings/SystemSetup/HasAcOutSystem', 0)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/RunWithoutGridMeter', 1)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', 20)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', -10)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', 30)
		self._add_device('com.victronenergy.grid.ttyUSB1', {
			'/Ac/L1/Power': 1230,
			'/Ac/L2/Power': 1130,
			'/Ac/L3/Power': 1030})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Ac/L2/Power': 400,
			'/Ac/L3/Power': 200,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/L2/Power': 1130,
			'/Ac/Grid/L3/Power': 1030,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 500 + 20,
			'/Ac/Consumption/L2/Power': 1130 + 400,
			'/Ac/Consumption/L3/Power': 1030 + 200 + 30,
			'/Ac/ConsumptionOnInput/L1/Power': 1230 - 123 + 500,
			'/Ac/ConsumptionOnInput/L2/Power': 1130 + 400,
			'/Ac/ConsumptionOnInput/L3/Power': 1030 + 200,
			'/Ac/ConsumptionOnOutput/NumberOfPhases': 3,
			'/Ac/ConsumptionOnOutput/L1/Power': 20,
			'/Ac/ConsumptionOnOutput/L2/Power': 0,
			'/Ac/ConsumptionOnOutput/L3/Power': 30
		})

	def test_ac_gridmeter_inactive(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', {'/Ac/L1/Power': 1230})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 2,
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/L1/Power': 1230 + 100 + 500,
			'/Ac/ConsumptionOnInput/L1/Power': 1230 + 500,
			'/Ac/ConsumptionOnOutput/L1/Power': 100,
			'/Ac/PvOnGrid/L1/Power': 500
		})

	def test_pv_on_output(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 1
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', -100)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/L1/Power': 500 - 100,
			'/Ac/ConsumptionOnInput/L1/Power': 0,
			'/Ac/ConsumptionOnOutput/L1/Power': 500 - 100,
			'/Ac/PvOnOutput/L1/Power': 500
		})

	def test_multiple_pv(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2313', {
			'/Ac/L2/Power': 200,
			'/Position': 1
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2314', {
			'/Ac/L1/Power': 105,
			'/Position': 1
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2315', {
			'/Ac/L3/Power': 300,
			'/Position': 1
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2316', {
			'/Ac/L1/Power': 110,
			'/Ac/L3/Power': 200,
			'/Position': 1
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2317', {
			'/Ac/L1/Power': 120,
			'/Ac/L2/Power': 220,
			'/Position': 0
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', -100)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/L1/Power': 123 - 120,
			'/Ac/Grid/L2/Power': -220,
			'/Ac/Grid/L3/Power': None,
			'/Ac/Grid/NumberOfPhases': 2,
			'/Ac/Consumption/L1/Power': 105 + 110 - 100,
			# No grid meter so assume that are no loads on ac input
			'/Ac/ConsumptionOnInput/L1/Power': 0,
			'/Ac/ConsumptionOnOutput/L1/Power': 105 + 110 + -100,
			'/Ac/PvOnOutput/NumberOfPhases': 3,
			'/Ac/PvOnOutput/L1/Power': 105 + 110,
			'/Ac/PvOnGrid/L1/Power': 120,
			'/Ac/PvOnGrid/L2/Power': 220,
			'/Ac/PvOnGrid/L3/Power': None,
			'/Ac/PvOnGrid/NumberOfPhases': 2
		})

	def test_pv_on_input_invalid(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 2
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', -500)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/L1/Power': 0,
			'/Ac/ConsumptionOnInput/L1/Power': 0,
			'/Ac/ConsumptionOnOutput/L1/Power': 0,
			'/Ac/PvOnGenset/L1/Power': 500
		})

	def test_dc_charger(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7
		})
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None,
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_multi_dc_power(self):
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': -8,
			'/Dc/Vebus/Power': -8 * 12.25})

	def test_multi_dc_power_2(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', -98.7)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': -8,
			'/Dc/Vebus/Power': -98.7})

	def test_multi_dc_power_3(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': None,
			'/Dc/Vebus/Power': None})

	def test_multi_dc_power_4(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', 6.5)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': 6.5,
			'/Dc/Vebus/Power': None})

	def test_dc_charger_battery(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 5.6,
								'/Dc/0/Power': 120})
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 1)

		self._update_values()
		self._check_values({
			'/Dc/System/Power': 12.4 * 9.7 - 120 - 12.25 * 8,
			'/Dc/Battery/Power': 120,
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_hub1(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Mgmt/Connection', "VE.Bus")
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.solarcharger.ttyO2',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 24.3,
								'/Dc/0/Current': 5.6})

		self._update_values()
		self._check_values({
			'/Hub': 1,
			'/SystemType': 'Hub-1',
			'/Dc/Pv/Power': 12.4 * 9.7 + 24.3 * 5.6})

	def test_hub1_vecan(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Mgmt/Connection', "VE.Can")
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Hub': 1,
			'/SystemType': 'Hub-1',
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_hub2(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 1
		})

		self._update_values()
		self._check_values({
			'/Hub': 2,
			'/SystemType': 'Hub-2',
			'/Ac/PvOnOutput/L1/Power': 500})

	def test_hub3_grid(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Hub': 3,
			'/SystemType': 'Hub-3',
			'/Ac/PvOnGrid/L1/Power': 500,
			'/Ac/Grid/L1/Power': 123 - 500,
			'/Ac/Genset/L1/Power': None})

	def test_hub3_genset(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 2
		})

		self._update_values()
		self._check_values({
			'/Hub': 3,
			'/SystemType': 'Hub-3',
			'/Ac/PvOnGenset/L1/Power': 500,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Genset/L1/Power': -500})

	def test_hub4_pv(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 3)
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 2
		})

		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/SystemType': 'Hub-4',
			'/Ac/PvOnGenset/L1/Power': 500})

	def test_ess_pv(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/SystemType': 'ESS'})

	def test_hub4_missing_pv(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', -500)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', -500)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 3)
		self._add_device('com.victronenergy.grid.ttyUSB1', {'/Ac/L1/Power': -300})

		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/SystemType': 'Hub-4',
			'/Ac/Consumption/L1/Power': 200,
			'/Ac/ConsumptionOnInput/L1/Power': 200,
			'/Ac/ConsumptionOnOutput/L1/Power': 0
			})

	def test_hub4_charger(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7
		})
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 3)

		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/SystemType': 'Hub-4',
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_serial(self):
		self._update_values()
		s = self._service['/Serial']
		self.assertEqual(len(s), 12)
		# Check if 's' is a hex string, if not an exception should be raised, causing the test to fail.
		self.assertIsNotNone(int(s, 16))

	def test_dc_current_from_power(self):
		self._update_values()
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Current': None,
			'/Dc/Battery/Voltage': 0,
			'/Dc/Battery/Power': 0})

	def test_battery_selection(self):
		self._update_values()
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': -8 * 12.25,
			'/Dc/Battery/Voltage': 12.25,
			'/ActiveBatteryService': 'com.victronenergy.vebus/0'})

	def test_battery_selection_default(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': -8 * 12.25,
			'/Dc/Battery/Voltage': 12.25,
			'/ActiveBatteryService': 'com.victronenergy.vebus/0'})

	def test_battery_selection_solarcharger(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Current': (9.7 * 12.4 - 12.25 * 8) / 12.4,
			'/Dc/Battery/Power': 9.7 * 12.4 - 12.25 * 8,
			'/Dc/Battery/Voltage': 12.4,
			'/ActiveBatteryService': None})

	def test_battery_selection_solarcharger_no_vebus_voltage(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Current': 9.7 * 12.4 / 12.4,
			'/Dc/Battery/Power': 9.7 * 12.4,
			'/Dc/Battery/Voltage': 12.4,
			'/ActiveBatteryService': None})

	def test_battery_selection_solarcharger_no_voltage(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': None,
								'/Dc/0/Current': None})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': - 12.25 * 8,
			'/Dc/Battery/Voltage': 12.25,
			'/ActiveBatteryService': None})

	def test_battery_selection_solarcharger_extra_current(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/Dc/Battery/Current': (12.4 * 9.7 - 12.25 * 8) / 12.4,
			'/Dc/Battery/Power': 12.4 * 9.7 - 12.25 * 8,
			'/Dc/Battery/Voltage': 12.4,
			'/ActiveBatteryService': 'com.victronenergy.vebus/0'})
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))

	def test_battery_selection_no_battery(self):
		self._update_values()
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': -8 * 12.25,
			'/Dc/Battery/Voltage': 12.25,
			'/ActiveBatteryService': None})

	def test_battery_no_battery2(self):
		self._update_values()
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/2')
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 1)
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None,
			'/Dc/Battery/Power': None,
			'/ActiveBatteryService': None})

	def test_battery_selection_wrong_format(self):
		self._set_setting('/Settings/SystemSetup/BatteryService', 'wrong format')
		self._update_values()
		available_measurements = json.loads(self._service['/AvailableBatteryServices'])
		self.assertEqual(len(available_measurements), 3)
		self.assertEqual(available_measurements['default'], 'Automatic')
		self.assertEqual(available_measurements['nobattery'], 'No battery monitor')
		self.assertEqual(available_measurements['com.victronenergy.vebus/0'], 'Multi on dummy')
		self._check_values({'/AutoSelectedBatteryService': None})

	def test_battery_no_battery_power(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
								'/Dc/0/Voltage': 12.4,
								'/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': None,
								'/Dc/0/Current': None,
								'/Dc/0/Power': None,
								'/DeviceInstance': 2})
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/2')
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 1)
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None,
			'/Dc/Battery/Power': None,
			'/Dc/Pv/Power': 12.4 * 9.7,
			'/ActiveBatteryService': 'com.victronenergy.battery/2'})

	def test_removed_services(self):
		# Sometimes a service is removed while systemcalc is doing its calculations. Net result is that
		# the D-Bus monitor will return None on items that were part of the service. This happens if the
		# service disappears after a list of services is retrieved and before values from services in that
		# list are used.
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', None)
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': None,
			'/Position': None
		})
		self._add_device('com.victronenergy.solarcharger.ttyO1',
				product_name='solarcharger',
				values={
						'/Dc/0/Voltage': None,
						'/Dc/0/Current': None})
		self._add_device('com.victronenergy.charger.ttyUSB2',
				product_name='charger',
				values={
						'/Dc/0/Voltage': None,
						'/Dc/0/Current': None})
		self._add_device('com.victronenergy.battery.ttyO2',
				product_name='battery',
				values={
						'/Dc/0/Voltage': None,
						'/Dc/0/Current': None,
						'/Dc/0/Power': None})
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 1)
		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': None,
			'/Ac/Grid/L1/Power': None,
			'/Ac/Grid/L2/Power': None,
			'/Ac/Grid/L3/Power': None,
			'/Ac/Genset/NumberOfPhases': None,
			'/Ac/Consumption/NumberOfPhases': None,
			'/Ac/ConsumptionOnInput/NumberOfPhases': None,
			'/Ac/ConsumptionOnOutput/NumberOfPhases': None,
			'/Ac/PvOnOutput/NumberOfPhases': None
		})

	def test_multiple_vebus_systems(self):
		self._add_device('com.victronenergy.vebus.ttyO2',
			product_name='Multi2',
			connected=False,
			values={
				'/Ac/ActiveIn/L1/P': None,
				'/Ac/ActiveIn/ActiveInput': None,
				'/Ac/Out/L1/P': None,
				'/Dc/0/Voltage': None,
				'/Dc/0/Current': None,
				'/DeviceInstance': None,
				'/Soc': None,
				'/State': 3
			})

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Consumption/L1/Power': 100
		})

	def test_multiple_vebus_systems_2(self):
		self._add_device('com.victronenergy.vebus.ttyO2',
			product_name='Multi2',
			connected=True,
			values={
				'/Ac/ActiveIn/L1/P': 127,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/Out/L1/P': 87,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/DeviceInstance': 1,
				'/Soc': 53.2,
				'/State': 3
			})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Connected', 0)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', None)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', None)

		self._update_values()
		self._check_values({
			'/Ac/Grid/L1/Power': 127,
			'/Ac/Consumption/L1/Power': 87
		})

	def test_disconnected_vebus_is_ignored_in_auto_mode(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Connected', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Voltage': 12.25})

	def test_connected_vebus_is_auto_selected(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/Dc/Battery/Voltage': 12.25,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': -98,
			'/AutoSelectedBatteryService': 'Multi on dummy'})

	def test_onlybattery_defaultsetting(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 15.3,
			'/Dc/Battery/Voltage': 12.3,
			'/Dc/Battery/Current': 5.3,
			'/Dc/Battery/Power': 65,
			'/AutoSelectedBatteryService': 'battery on dummy'})

	def test_batteryandvebus_defaultsetting(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/AutoSelectedBatteryService': 'Multi on dummy'})
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 15.3,
			'/AutoSelectedBatteryService': 'battery on dummy'})

		self._monitor.remove_service('com.victronenergy.battery.ttyO2')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2,
			'/AutoSelectedBatteryService': 'Multi on dummy'})

	def test_battery_voltage_vebus(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.25})

	def test_battery_voltage_solarcharger(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.4})

	def test_battery_voltage_charger(self):
		self._add_device('com.victronenergy.charger.ttyO1',
						product_name='charger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.4})

	def test_battery_voltage_sequence(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.25})

		self._update_values()
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.4})

		self._add_device('com.victronenergy.charger.ttyO1',
						product_name='charger',
						values={
							'/Dc/0/Voltage': 12.7,
							'/Dc/0/Current': 6.3})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.4})

		self._monitor.remove_service('com.victronenergy.solarcharger.ttyO1')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.7})

		self._monitor.remove_service('com.victronenergy.charger.ttyO1')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Voltage': 12.25})

	def test_do_not_autoselect_vebus_soc_when_charger_is_present(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2})

		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None})

		self._add_device('com.victronenergy.charger.ttyO1',
						product_name='charger',
						values={
							'/Dc/0/Voltage': 12.7,
							'/Dc/0/Current': 6.3})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None})

		self._monitor.remove_service('com.victronenergy.charger.ttyO1')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None})

		self._monitor.remove_service('com.victronenergy.solarcharger.ttyO1')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': 53.2})

	def test_when_hasdcsystem_is_disabled_system_should_be_invalid(self):
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 0)
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None})

	def test_calculation_of_dc_system(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.charger.ttyO1',
						product_name='charger',
						values={
							'/Dc/0/Voltage': 12.7,
							'/Dc/0/Current': 6.3})
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 1)
		self._update_values()
		self._check_values({
			'/Dc/System/Power':  12.7 * 6.3 + 12.4 * 9.7 - 12.25 * 8 - 65})

		self._monitor.remove_service('com.victronenergy.battery.ttyO2')
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None})

	def test_battery_state(self):
		self._check_values({
			'/Dc/Battery/State':  None})
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Power', 40)
		self._update_values()
		self._check_values({
			'/Dc/Battery/State':  1})

		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Power', -40)
		self._update_values()
		self._check_values({
			'/Dc/Battery/State':  2})

		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Power', 1)
		self._update_values()
		self._check_values({
			'/Dc/Battery/State':  0})

	def test_derive_battery(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						product_name='solarcharger',
						values={
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.charger.ttyO1',
						product_name='charger',
						values={
							'/Dc/0/Voltage': 12.7,
							'/Dc/0/Current': 6.3})
		self._set_setting('/Settings/SystemSetup/HasDcSystem', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  12.4 * 9.7 + 12.7 * 6.3 - 12.25 * 8,
			'/Dc/Battery/Current':  (12.4 * 9.7 + 12.7 * 6.3 - 12.25 * 8) / 12.4,
			'/Dc/Battery/Voltage':  12.4})

	def test_available_battery_measurement(self):
		self._update_values()
		available_measurements = self._service['/AvailableBatteryMeasurements']
		self.assertEqual(len(available_measurements), 3)
		self.assertEqual(available_measurements['default'], 'Automatic')
		self.assertEqual(available_measurements['nobattery'], 'No battery monitor')
		self.assertEqual(available_measurements['com_victronenergy_vebus_0/Dc/0'], 'Multi on dummy')
		self._check_values({'/AutoSelectedBatteryMeasurement': 'com_victronenergy_vebus_0/Dc/0'})

	def test_available_battery_measurement_2(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		available_measurements = self._service['/AvailableBatteryMeasurements']
		self.assertEqual(len(available_measurements), 4)
		self.assertEqual(available_measurements['com_victronenergy_battery_2/Dc/0'], 'battery on dummy')
		self._check_values({'/AutoSelectedBatteryMeasurement': 'com_victronenergy_battery_2/Dc/0'})

	def test_available_battery_measurement_3(self):
		self._update_values()
		available_measurements = self._service['/AvailableBatteryMeasurements']
		self.assertEqual(len(available_measurements), 3)
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		available_measurements = self._service['/AvailableBatteryMeasurements']
		self.assertEqual(len(available_measurements), 4)
		self.assertEqual(available_measurements['com_victronenergy_battery_2/Dc/0'], 'battery on dummy')
		self._check_values({'/AutoSelectedBatteryMeasurement': 'com_victronenergy_battery_2/Dc/0'})

	def test_pv_inverter_ids_empty(self):
		self._update_values()
		self.assertEqual([], self._service['/PvInvertersProductIds'])

	def test_pv_inverter_ids(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0,
			'/ProductId': 0xB0FE
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2311', {
			'/Ac/L1/Power': 500,
			'/Position': 0,
			'/ProductId': 0xB0FF
		})
		self._update_values()
		self.assertEqual([0xB0FE, 0xB0FF], self._service['/PvInvertersProductIds'])

	def test_pv_inverter_ids_identical(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0,
			'/ProductId': 0xB0FE
		})
		self._add_device('com.victronenergy.pvinverter.fronius_122_2311', {
			'/Ac/L1/Power': 500,
			'/Position': 0,
			'/ProductId': 0xB0FE
		})
		self._update_values()
		self.assertEqual([0xB0FE], self._service['/PvInvertersProductIds'])

	def test_hub1_control_voltage_with_state(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.6)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0xE117},
			connection='VE.Direct')
		self._update_values()
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/VoltageSense': 12.25,
				'/State': 2
			}})
		self._check_values({
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1})

	def test_hub1_control_voltage_without_state(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.6)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0119},
			connection='VE.Direct')
		self._update_values()
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': 12.6,
				'/Link/VoltageSense': 12.25,
				'/State': 0
			}})
		self._check_values({
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1})

	def test_hub1_control_voltage_multiple_solarchargers(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.5)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0117},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1',
			'/Link/ChargeVoltage'))
		self.assertEqual(12.5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self.assertEqual(2, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1', '/State'))
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2', '/State'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_voltage_ve_can_solarchargers(self):
		# Hub1 control should ignore VE.Can solarchargers
		# self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.5)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._update_values()
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1',
			'/Link/ChargeVoltage'))
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.solarcharger.ttyO1', '/State'))
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_ve_can_service(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.can0', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': None})
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._add_device('com.victronenergy.vecan.can1', {
			'/Link/ChargeVoltage': None})
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(13.2, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/ChargeVoltage'))
		self._remove_device('com.victronenergy.vecan.can0')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.1)
		self._update_values(interval=10000)
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(13.1, self._monitor.get_value('com.victronenergy.vecan.can1', '/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_ve_can_service_no_solar_charger(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': None})
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 13.2)
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_ve_can_and_solar_charger(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.63)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.solarcharger.can0', {
			'/State': 0,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7},
			connection='VE.Can')
		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': 12.3})
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(12.63, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.53)
		self._update_values(interval=10000)
		self.assertEqual(5, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2', '/Link/NetworkMode'))
		self.assertEqual(12.53, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self.assertEqual(12.53, self._monitor.get_value('com.victronenergy.solarcharger.ttyO2',
			'/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeVoltage': 1})

	def test_hub1_control_ve_can_service_no_setpoint(self):
		self._update_values()
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 12.65)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 2)
		self._add_device('com.victronenergy.vecan.can0', {}, connection='VE.Can')
		self._update_values()
		self.assertEqual(None, self._monitor.get_value('com.victronenergy.vecan.can0', '/Link/ChargeVoltage'))
		self._check_values({'/Control/SolarChargeCurrent': 0})
		self._check_values({'/Control/SolarChargeVoltage': 0})

	def test_hub1_control_vedirect_solarcharger_bms_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.1,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_vedirect_solarcharger_bms_battery_max_charge_current_setting(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 0)
		self._set_setting('/Settings/SystemSetup/MaxChargeCurrent', 20)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 58.0,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 20 + 8,
				'/Link/ChargeVoltage': 55.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_battery_no_charge_voltage(self):
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 31,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 58.2,
				'/Link/VoltageSense': 12.25},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/SolarChargerVoltageSense': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_charge_distribution(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Dc/0/MaxChargeCurrent', 0)
		self._update_values()
		self._add_device('com.victronenergy.solarcharger.ttyO0', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': 15,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 14.3,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': 15,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 7,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO0': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 - 7 + 8,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 7 * 1.1 / 0.9,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': math.floor((25 - 14.3 - 7) * 0.8)}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_ess_feedback(self):
		# When feedback is allowed we do not limit MPPTs
		# Force system type to ESS
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 58.3)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 1)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 1,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 31,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.0,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 100,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 25}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_control_vedirect_solarcharger_bms_ess_feedback_no_ac_in(self):
		# When feedback is allowed we do not limit MPPTs, but in this case there is no AC-in so feedback is
		# not possible.
		# Force system type to ESS
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 58.3)
		self._monitor.add_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn', 1)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/Connected', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 1,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': 57.3,
			'/Link/ChargeCurrent': 20,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 31,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 58.0,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25 + 8,
				'/Link/ChargeVoltage': 58.3},
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 0}})
		self._check_values({
			'/SystemType': 'ESS',
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 1})

	def test_hub1_control_vedirect_solarcharger_bms_battery_no_link(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub/ChargeVoltage', 55.2)
		# Solar chargers with firmware < 1.17 do not publish the /Link section.
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Settings/ChargeCurrentLimit': 100,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 30,
			'/FirmwareVersion': 0x0116},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 25}})
		self._check_values({
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargeVoltage': 0,
			'/Control/BmsParameters': 1})

	def test_hub1_control_vedirect_solarcharger_bms_battery_no_solarcharger(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatteryOperationalLimits/BatteryLowVoltage': 47,
				'/BatteryOperationalLimits/MaxChargeCurrent': 25,
				'/BatteryOperationalLimits/MaxChargeVoltage': 58.2,
				'/BatteryOperationalLimits/MaxDischargeCurrent': 50,
				'/Dc/0/MaxChargeCurrent': 25}})
		self._check_values({
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargeVoltage': 0,
			'/Control/BmsParameters': 1})

	def test_system_mapping(self):
		self._update_values()
		self._check_values({
			'/ServiceMapping/com_victronenergy_vebus_0': 'com.victronenergy.vebus.ttyO1',
			'/ServiceMapping/com_victronenergy_settings_0': 'com.victronenergy.settings'})
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery', values={'/DeviceInstance': 3})
		self._check_values({
			'/ServiceMapping/com_victronenergy_vebus_0': 'com.victronenergy.vebus.ttyO1',
			'/ServiceMapping/com_victronenergy_battery_3': 'com.victronenergy.battery.ttyO2'})
		self._remove_device('com.victronenergy.battery.ttyO2')
		self.assertFalse('/ServiceMapping/com_victronenergy_battery_3' in self._service)

	def test_hub1_extra_current(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(9.7 + 9.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/ExtraBatteryCurrent'))

	def test_hub1_extra_current_no_battery_no_solarcharger(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 1)
		self._update_values()
		self.assertEqual(0, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({
			'/Control/ExtraBatteryCurrent': 1,
			'/Control/SolarChargeVoltage': 0,
			'/Control/SolarChargeCurrent': 0,
			'/Control/SolarChargerVoltageSense': 0})

	def test_hub1_extra_current_hub2_no_battery_monitor(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.3},
			connection='VE.Direct')
		self._update_values()
		self.assertEqual(9.7 + 9.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1',
			'/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})

	def test_hub1_no_extra_current(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', None)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values()
		self.assertIsNone(self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})

	def test_hub1_with_bmv_extra_current_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_hub2_extra_current_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 0)
		# Set hub-2 & Lynx Ion assistant
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants',
			[0x4D, 0x01, 0x3C, 0x01] + (26 * [0]))
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values(3000)
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({
			'/Control/ExtraBatteryCurrent': 1,
			'/Control/VebusSoc': 0,
			'/Control/SolarChargerVoltageSense': 1,
			'/Control/SolarChargeVoltage': 0,
			'/Control/SolarChargeCurrent': 0,
			'/Control/BmsParameters': 0})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/ChargeVoltage': None,
				'/Link/ChargeCurrent': None,
				'/Link/VoltageSense': 12.25}})

	def test_hub1_extra_current_no_active_battery(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_hub1_extra_current_write_soc(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.battery/2')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_hub1_extra_current_no_write_soc(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent', 23)
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self.assertEqual(9.7, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/ExtraBatteryCurrent'))
		self._check_values({'/Control/ExtraBatteryCurrent': 1})
		self._check_values({'/Control/VebusSoc': 0})

	def test_vebus_soc_writer(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(15.3, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 1})

	def test_vebus_soc_writer_hub2(self):
		# Set hub-2 & Input current control
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants',
			[0x46, 0x01, 0x00, 0x00, 0x4D, 0x01] + (24 * [0]))
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})

	def test_vebus_soc_writer_no_assistant_ids(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants', None)
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})

	def test_vebus_soc_writer_vebus(self):
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self.assertEqual(53.2, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._update_values(10000)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', 54)
		self._update_values(10000)
		self.assertEqual(54, self._monitor.get_value('com.victronenergy.vebus.ttyO1', '/Soc'))
		self._check_values({'/Control/ExtraBatteryCurrent': 0})
		self._check_values({'/Control/VebusSoc': 0})

	def test_gpio_buzzer(self):
		with tempfile.NamedTemporaryFile(mode='wt') as gpio_buzzer_ref_fd:
			gpio_dir = tempfile.mkdtemp()
			gpio_state = os.path.join(gpio_dir, 'value')
			with file(gpio_state, 'wt') as f:
				f.write('0')
			try:
				gpio_buzzer_ref_fd.write(gpio_dir)
				gpio_buzzer_ref_fd.flush()
				delegates.BuzzerControl.GPIO_BUZZER_PATH = gpio_buzzer_ref_fd.name
				bc = delegates.BuzzerControl()
				bc.set_sources(self._monitor, self._system_calc._settings, self._service)
				self.assertEqual(bc._pwm_frequency, None)
				self.assertEqual(bc._gpio_path, gpio_state)
				self._service.set_value('/Buzzer/State', 'aa')  # Invalid value, should be ignored
				self.assertEqual(self._service['/Buzzer/State'], 0)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
				self._service.set_value('/Buzzer/State', '1')
				self.assertEqual(self._service['/Buzzer/State'], 1)
				self.assertEqual(file(gpio_state, 'rt').read(), '1')
				self._update_values(interval=505)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
				self._update_values(interval=505)
				self.assertEqual(file(gpio_state, 'rt').read(), '1')
				self._service.set_value('/Buzzer/State', 0)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
			finally:
				os.remove(gpio_state)
				os.removedirs(gpio_dir)

	def test_pwm_buzzer(self):
		# This test will log an exception to the standard output, because the BuzzerControl tries to do
		# a ioctl on a regular file (a temp file created for this test), which is not allowed. We use
		# a regular file here because we do not want to enable the buzzer on the machine running this
		# unit test.
		with tempfile.NamedTemporaryFile(mode='wt') as pwm_buzzer_fd, \
				tempfile.NamedTemporaryFile(mode='wt') as tty_path_fd:
			pwm_buzzer_fd.write('400')
			pwm_buzzer_fd.flush()
			delegates.BuzzerControl.PWM_BUZZER_PATH = pwm_buzzer_fd.name
			delegates.BuzzerControl.TTY_PATH = tty_path_fd.name
			bc = delegates.BuzzerControl()
			bc.set_sources(self._monitor, self._system_calc._settings, self._service)
			self.assertEqual(bc._pwm_frequency, 400)
			self.assertEqual(bc._gpio_path, None)

	def test_pwm_buzzer_invalid_etc_file(self):
		with tempfile.NamedTemporaryFile(mode='wt') as pwm_buzzer_fd:
			pwm_buzzer_fd.write('xx')
			pwm_buzzer_fd.flush()
			delegates.BuzzerControl.PWM_BUZZER_PATH = pwm_buzzer_fd.name
			bc = delegates.BuzzerControl()
			bc.set_sources(self._monitor, self._system_calc._settings, self._service)
			self.assertEqual(bc._pwm_frequency, None)
			self.assertEqual(bc._gpio_path, None)


class TestSystemCalcNoMulti(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def test_noservices(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/AutoSelectedBatteryService': 'No battery monitor found'})

	def test_no_battery_service(self):
		self._set_setting('/Settings/SystemSetup/BatteryService', 'nobattery')
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  None,
			'/AutoSelectedBatteryService': None})

		self._set_setting('/Settings/SystemSetup/BatteryService', 'default')
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  65,
			'/AutoSelectedBatteryService': 'battery on dummy'})

	def test_hub1_control_vedirect_solarcharger_bms_battery(self):
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 24,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25,
				'/Link/ChargeVoltage': 58.2}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 0})

	def test_hub1_control_bms_battery_vedirect_solarcharger_off(self):
		self._add_device('com.victronenergy.solarcharger.ttyO0', {
			'/State': 0,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 0,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 3,
			'/Settings/ChargeCurrentLimit': 100,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 24,
			'/FirmwareVersion': 0x0118},
			connection='VE.Direct')
		self._add_device('com.victronenergy.battery.ttyUSB0',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.3,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2,
				'/Info/BatteryLowVoltage': 47,
				'/Info/MaxChargeCurrent': 25,
				'/Info/MaxChargeVoltage': 58.2,
				'/Info/MaxDischargeCurrent': 50})
		self._update_values(interval=10000)
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO0': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': None,
				'/Link/ChargeVoltage': 58.2},
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/NetworkMode': 13,
				'/Link/ChargeCurrent': 25,
				'/Link/ChargeVoltage': 58.2}})
		self._check_values({
			'/Control/SolarChargeCurrent': 1,
			'/Control/SolarChargeVoltage': 1,
			'/Control/BmsParameters': 0})

	def test_hub1bridge_distr_1(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 3)
		self.assertEqual(new_values, [2, 3, 4])

	def test_hub1bridge_distr_2(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 9.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_3(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 10.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_4(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [3.5, 4.5, 4])

	def test_hub1bridge_distr_5(self):
		actual_values = [3, 2, 1]
		max_values = [4, 5, 6]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [4, 4.5, 3.5])

	def test_hub1bridge_distr_6(self):
		actual_values = [4, 5, 6]
		max_values = [1, 2, 8]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 0.0)
		self.assertEqual(new_values, [1, 2, 8])

	def test_hub1bridge_distr_7(self):
		actual_values = [1]
		max_values = [5]
		new_values = delegates.Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [5])


if __name__ == '__main__':
	unittest.main()
