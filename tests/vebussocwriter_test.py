#!/usr/bin/env python

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestHubSystem(TestSystemCalcBase):
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
			'/Load/I': 5,
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
			[0x33, 0x01, 0x00, 0x00, 0x4D, 0x01] + (24 * [0]))
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

	def test_vebus_soc_writer_no_hub2(self):
		# Set hub-2 & Input current control
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Devices/0/Assistants',
			[0x88, 0x01, 0x00, 0x00, 0x4D, 0x00] + (24 * [0]))
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Hub2', None)
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
