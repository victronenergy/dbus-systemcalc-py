#!/usr/bin/env python
import os
import platform
import sys
import unittest

# our own packages
test_dir = os.path.dirname(__file__)
sys.path.insert(1, os.path.join(test_dir, '..', 'ext', 'velib_python', 'test'))
sys.path.insert(1, os.path.join(test_dir, '..'))
import dbus_systemcalc
import vedbus
from logger import setup_logging
from mock_dbus_monitor import MockDbusMonitor
from mock_dbus_service import MockDbusService
from mock_settings_device import MockSettingsDevice


dbus_systemcalc.logger = setup_logging()


class TestSystemCalcBase(unittest.TestCase):
	def __init__(self, methodName='runTest'):
		unittest.TestCase.__init__(self, methodName)
		self._service = MockDbusService('com.victronenergy.system')
		self._system_calc = dbus_systemcalc.SystemCalc(\
			lambda x: MockDbusMonitor(x), \
			lambda x: MockDbusService(x), \
			lambda x, y: MockSettingsDevice(x, y))
		self._monitor = self._system_calc._dbusmonitor
		self._service = self._system_calc._dbusservice
		self._settings = self._system_calc._settings

	def _update_values(self):
		self._system_calc._determinebatteryservice()
		self._system_calc._handleservicechange()
		self._system_calc._updatevalues()

	def _add_device(self, service, values, connected=True, product_name='dummy', connection='dummy'):
		self._monitor.set_value(service, '/Connected', 1 if connected else 0)
		self._monitor.set_value(service, '/ProductName', product_name)
		self._monitor.set_value(service, '/Mgmt/Connection', connection)
		for k, v in values.items():
			self._monitor.set_value(service, k, v)

	def _check_values(self, values):
		for k, v in values.items():
			self.assertEqual(self._service[k], v)


class TestSystemCalc(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)
		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='Multi',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/DeviceInstance': 0,
				'/Soc': 53.2,
				'/State': 3
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
			'/Ac/Grid/Total/Power': 123,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/L2/Power': None,
			'/Ac/Grid/L3/Power': None,
			'/Ac/Genset/Total/Power': None,
			'/Ac/Consumption/Total/Power': 100,
			'/Ac/Consumption/L1/Power': 100,
			'/Ac/Consumption/L2/Power': None,
			'/Ac/Consumption/L3/Power': None
		})

	def test_ac_in_genset(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 2,
			'/Ac/Genset/Total/Power': 123,
			'/Ac/Genset/L1/Power': 123,
			'/Ac/Grid/Total/Power': None
		})

	def test_ac_in_not_available(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 0)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 0,
			'/Ac/Grid/Total/Power': None,
			'/Ac/Genset/Total/Power': None
		})

	def test_ac_in_shore(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 3)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 3,
			'/Ac/Grid/Total/Power': 123,
			'/Ac/Genset/Total/Power': None
		})

	def test_ac_in_grid_3p(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', 100)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', 150)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', 200)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', 80)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', 90)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', 100)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 1,
			'/Ac/Grid/Total/Power': 450,
			'/Ac/Grid/L1/Power': 100,
			'/Ac/Grid/L2/Power': 150,
			'/Ac/Grid/L3/Power': 200,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Genset/L1/Power': None,
			'/Ac/Genset/Total/Power': None,
			'/Ac/Genset/NumberOfPhases': None,
			'/Ac/Consumption/L1/Power': 80,
			'/Ac/Consumption/L2/Power': 90,
			'/Ac/Consumption/L3/Power': 100
		})

	def test_ac_gridmeter(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', { '/Ac/L1/Power': 1230 })
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/Total/Power': 1230,
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/Total/Power': 1230 - 123 + 100 + 500,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 100 + 500
		})

	def test_ac_gridmeter_3p(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', {
			'/Ac/L1/Power': 1230,
			'/Ac/L2/Power': 1130,
			'/Ac/L3/Power': 1030 })
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Ac/L2/Power': 400,
			'/Ac/L3/Power': 200,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Ac/Grid/Total/Power': 1230 + 1130 + 1030,
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/L2/Power': 1130,
			'/Ac/Grid/L3/Power': 1030,
			'/Ac/Grid/NumberOfPhases': 3,
			'/Ac/Consumption/Total/Power': 1230 + 1130 + 1030 - 123 + 100 + 500 + 400 + 200,
			'/Ac/Consumption/L1/Power': 1230 - 123 + 100 + 500,
			'/Ac/Consumption/L2/Power': 1130 + 400,
			'/Ac/Consumption/L3/Power': 1030 + 200
		})

	def test_ac_gridmeter_inactive(self):
		self._add_device('com.victronenergy.grid.ttyUSB1', { '/Ac/L1/Power': 1230 })
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)

		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': 2,
			'/Ac/Grid/Total/Power': 1230,
			'/Ac/Grid/L1/Power': 1230,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/Total/Power': 1230 + 100 + 500,
			'/Ac/Consumption/L1/Power': 1230 + 100 + 500,
			'/Ac/PvOnGrid/Total/Power': 500,
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
			'/Ac/Grid/Total/Power': 123,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/Total/Power': 500 - 100,
			'/Ac/Consumption/L1/Power': 500 - 100,
			'/Ac/PvOnOutput/Total/Power': 500,
			'/Ac/PvOnOutput/L1/Power': 500
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
			'/Ac/Grid/Total/Power': 123,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Grid/NumberOfPhases': 1,
			'/Ac/Consumption/Total/Power': 0,
			'/Ac/Consumption/L1/Power': 0,
			'/Ac/PvOnGenset/Total/Power': 500,
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
			'/Dc/Vebus/Power': -8 * 12.25 })

	def test_multi_dc_power_2(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', -98.7)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': -8,
			'/Dc/Vebus/Power': -98.7 })

	def test_multi_dc_power_3(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': None,
			'/Dc/Vebus/Power': None })

	def test_multi_dc_power_4(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Power', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', 6.5)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._update_values()
		self._check_values({
			'/Dc/Vebus/Current': 6.5,
			'/Dc/Vebus/Power': None })

	def test_dc_charger_battery(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						 product_name='solarcharger',
						 values={
								 '/Dc/0/Voltage' : 12.4,
								 '/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.battery.ttyO2',
						 product_name='battery',
						 values={
								 '/Dc/0/Voltage' : 12.4,
								 '/Dc/0/Current': 5.6,
								 '/Dc/0/Power': 120})
		self._settings['hasdcsystem'] = 1

		self._update_values()
		self._check_values({
			'/Dc/System/Power': 12.4 * 9.7 - 120 - 12.25 * 8,
			'/Dc/Battery/Power': 120,
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_hub1(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						 product_name='solarcharger',
						 values={
								 '/Dc/0/Voltage' : 12.4,
								 '/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.solarcharger.ttyO2',
						 product_name='solarcharger',
						 values={
								 '/Dc/0/Voltage' : 24.3,
								 '/Dc/0/Current': 5.6})

		self._update_values()
		self._check_values({
			'/Hub': 1,
			'/Dc/Pv/Power': 12.4 * 9.7 + 24.3 * 5.6})

	def test_hub2(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 1
		})

		self._update_values()
		self._check_values({
			'/Hub': 2,
			'/Ac/PvOnOutput/Total/Power': 500})

	def test_hub3_grid(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 0
		})

		self._update_values()
		self._check_values({
			'/Hub': 3,
			'/Ac/PvOnGrid/Total/Power': 500,
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
			'/Ac/PvOnGenset/Total/Power': 500,
			'/Ac/Grid/L1/Power': 123,
			'/Ac/Genset/L1/Power': -500})

	def test_hub4_pv(self):
		self._add_device('com.victronenergy.pvinverter.fronius_122_2312', {
			'/Ac/L1/Power': 500,
			'/Position': 2
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AcPowerSetpoint', 100)

		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/Ac/PvOnGenset/Total/Power': 500})

	def test_hub4_charger(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7
		})
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AcPowerSetpoint', 100)

		self._update_values()
		self._check_values({
			'/Hub': 4,
			'/Dc/Pv/Power': 12.4 * 9.7})

	def test_serial(self):
		self._update_values()
		s = self._service['/Serial']
		self.assertEqual(len(s), 12)
		# Check if 's' is a hex string, if not an exception should be raised, causing the test to fail.
		self.assertIsNotNone(int(s, 16))

	def test_dc_current_from_power(self):
		self._update_values()
		self._settings['batteryservice'] = 'nobattery'
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', 0)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Current': None,
			'/Dc/Battery/Voltage': 0,
			'/Dc/Battery/Power': 0})

	def test_battery_selection(self):
		self._update_values()
		self._settings['batteryservice'] = 'com.victronenergy.vebus/0'
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

	def test_battery_selection_no_battery(self):
		self._update_values()
		self._settings['batteryservice'] = 'nobattery'
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/Dc/Battery/Current': -8,
			'/Dc/Battery/Power': -8 * 12.25,
			'/Dc/Battery/Voltage': 12.25,
			'/ActiveBatteryService': None})

	def test_battery_no_battery2(self):
		self._update_values()
		self._settings['batteryservice'] = 'com.victronenergy.battery/2'
		self._settings['hasdcsystem'] = 1
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None,
			'/Dc/Battery/Power': None,
			'/ActiveBatteryService': None})

	def test_battery_no_battery_power(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1',
						 product_name='solarcharger',
						 values={
								 '/Dc/0/Voltage' : 12.4,
								 '/Dc/0/Current': 9.7})
		self._add_device('com.victronenergy.battery.ttyO2',
						 product_name='battery',
						 values={
								 '/Dc/0/Voltage' : None,
								 '/Dc/0/Current': None,
								 '/Dc/0/Power': None,
								 '/DeviceInstance': 2})
		self._settings['batteryservice'] = 'com.victronenergy.battery/2'
		self._settings['hasdcsystem'] = 1
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
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', None)
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
						 '/Dc/0/Voltage' : None,
						 '/Dc/0/Current' : None})
		self._add_device('com.victronenergy.charger.ttyUSB2',
				 product_name='charger',
				 values={
						 '/Dc/0/Voltage' : None,
						 '/Dc/0/Current' : None})
		self._add_device('com.victronenergy.battery.ttyO2',
				 product_name='battery',
				 values={
						 '/Dc/0/Voltage' : None,
						 '/Dc/0/Current': None,
						 '/Dc/0/Power': None})
		self._settings['batteryservice'] = 'com.victronenergy.vebus/0'
		self._settings['hasdcsystem'] = 1
		self._update_values()
		self._check_values({
			'/Ac/ActiveIn/Source': None,
			'/Ac/Grid/Total/Power': None,
			'/Ac/Grid/L1/Power': None,
			'/Ac/Grid/L2/Power': None,
			'/Ac/Grid/L3/Power': None,
			'/Ac/Genset/Total/Power': None,
			'/Ac/Consumption/Total/Power': None,
			'/Ac/PvOnOutput/Total/Power': None
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
			'/Ac/Grid/Total/Power': 123,
			'/Ac/Consumption/Total/Power': 100
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
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', None)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', None)

		self._update_values()
		self._check_values({
			'/Ac/Grid/Total/Power': 127,
			'/Ac/Consumption/Total/Power': 87
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
								 '/Dc/0/Voltage' : 12.3,
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
								 '/Dc/0/Voltage' : 12.3,
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
		self._settings['hasdcsystem'] = 0
		self._update_values()
		self._check_values({
			'/Dc/System/Power': None})

	def test_calculation_of_dc_system(self):
		self._add_device('com.victronenergy.battery.ttyO2',
						 product_name='battery',
						 values={
								 '/Dc/0/Voltage' : 12.3,
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
		self._settings['hasdcsystem'] = 1
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
								 '/Dc/0/Voltage' : 12.3,
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
		self._settings['hasdcsystem'] = 0
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  12.4 * 9.7 + 12.7 * 6.3 - 12.25 * 8,
			'/Dc/Battery/Current':  (12.4 * 9.7 + 12.7 * 6.3 - 12.25 * 8) / 12.4,
			'/Dc/Battery/Voltage':  12.4})

	def test_single_redflow_battery(self):
		self._add_device('com.victronenergy.battery.redflow_1213',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 1,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 114,
							'/Soc': 83.4})
		self._update_values()
		self._check_values({
			'/AutoSelectedBatteryService': 'Redflow batteries',
			'/ActiveBatteryService': 'redflow',
			'/Dc/Battery/Power': 114,
			'/Dc/Battery/Soc':  83.4})

	def test_multiple_redflow_batteries(self):
		self._add_device('com.victronenergy.battery.redflow_1213',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 1,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 114,
							'/Soc': 83.4})
		self._add_device('com.victronenergy.battery.redflow_1215',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 2,
							'/Dc/0/Voltage': 13.7,
							'/Dc/0/Current': 6.7,
							'/Dc/0/Power': 121,
							'/Soc': 82.4})
		self._add_device('com.victronenergy.battery.redflow_1210',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 3,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 14,
							'/Soc': 80.4})
		self._update_values()
		self._check_values({
			'/AutoSelectedBatteryService': 'Redflow batteries',
			'/ActiveBatteryService': 'redflow',
			'/Dc/Battery/Power': 114 + 14 + 121,
			'/Dc/Battery/Soc':  (83.4 + 82.4 + 80.4)/3})

	def test_multiple_redflow_batteries_disconnected(self):
		self._add_device('com.victronenergy.battery.redflow_1213',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 1,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 114,
							'/Soc': 83.4})
		self._add_device('com.victronenergy.battery.redflow_1215',
						 product_name='battery',
						 values={
							'/Capabilities': 'IntegratedSoc,Redflow',
							'/DeviceInstance': 2,
							'/Dc/0/Voltage': 13.7,
							'/Dc/0/Current': 6.7,
							'/Dc/0/Power': 121,
							'/Soc': 82.4})
		self._add_device('com.victronenergy.battery.redflow_1210',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 3,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 14,
							'/Soc': 80.4})
		self._monitor.set_value('com.victronenergy.battery.redflow_1210', '/Connected', 0)
		self._update_values()
		self._check_values({
			'/AutoSelectedBatteryService': 'Redflow batteries',
			'/ActiveBatteryService': 'redflow',
			'/Dc/Battery/Power': 114 + 121,
			'/Dc/Battery/Soc':  (83.4 + 82.4)/2})

	def test_mixed_redflow_batteries(self):
		self._add_device('com.victronenergy.battery.redflow_1213',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 1,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 114,
							'/Soc': 83.4})
		self._add_device('com.victronenergy.battery.ttyUSB0',
						 product_name='battery',
						 values={
							'/DeviceInstance': 2,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 104,
							'/Soc': 81.4})
		self._update_values()
		self._check_values({
			'/AutoSelectedBatteryService': 'battery on dummy',
			'/ActiveBatteryService': 'com.victronenergy.battery/2',
			'/Dc/Battery/Power': 104,
			'/Dc/Battery/Soc':  81.4})

	def test_mixed_redflow_batteries_forced(self):
		self._add_device('com.victronenergy.battery.redflow_1213',
						 product_name='battery',
						 values={
							'/Capabilities': 'Redflow,IntegratedSoc',
							'/DeviceInstance': 1,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 114,
							'/Soc': 83.4})
		self._add_device('com.victronenergy.battery.ttyUSB0',
						 product_name='battery',
						 values={
							'/DeviceInstance': 2,
							'/Dc/0/Voltage': 12.4,
							'/Dc/0/Current': 9.7,
							'/Dc/0/Power': 104,
							'/Soc': 81.4})
		self._settings['batteryservice'] = 'redflow'
		self._update_values()
		self._check_values({
			'/AutoSelectedBatteryService': 'Redflow batteries',
			'/ActiveBatteryService': 'redflow',
			'/Dc/Battery/Power': 114,
			'/Dc/Battery/Soc':  83.4})


class TestSystemCalcNoMulti(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def test_noservices(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/AutoSelectedBatteryService': 'No battery monitor found'})

	def test_no_battery_service(self):
		self._settings['batteryservice'] = 'nobattery'
		self._add_device('com.victronenergy.battery.ttyO2',
						 product_name='battery',
						 values={
								 '/Dc/0/Voltage' : 12.3,
								 '/Dc/0/Current': 5.3,
								 '/Dc/0/Power': 65,
								 '/Soc': 15.3,
								 '/DeviceInstance': 2})
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  None,
			'/AutoSelectedBatteryService': None})

		self._settings['batteryservice'] = 'default'
		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  65,
			'/AutoSelectedBatteryService': 'battery on dummy'})


if __name__ == '__main__':
	unittest.main()
