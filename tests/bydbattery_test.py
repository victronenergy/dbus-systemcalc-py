import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches


class BydBatteryCurrentBackTest(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.vebus.ttyO1', product_name='Multi',
		values={
			'/Ac/ActiveIn/L1/P': 123,
			'/Ac/ActiveIn/ActiveInput': 0,
			'/Ac/ActiveIn/Connected': 1,
			'/Ac/Out/L1/P': 100,
			'/Dc/0/Voltage': 12.25,
			'/Dc/0/Current': -8.0,
			'/DeviceInstance': 0,
			'/Hub4/AssistantId': 5,
			'/Hub4/Sustain': 0,
			'/Dc/0/MaxChargeCurrent': None,
			'/Soc': 53.2,
			'/State': 3,  # Bulk
			'/VebusMainState': 9})

		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.5,
			'/FirmwareVersion': 0x129},
			connection='VE.Direct')

	@unittest.skip("Disabled for now")
	def test_sense_current(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/Sense/Current': None,
				'/DeviceInstance': 2,
				'/ProductId': 0xB00A}) # BYD
		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.battery.ttyO2': {
				'/Sense/Current': 1.5
			}
		})

	@unittest.skip("Disabled for now")
	def test_sense_current_only_byd(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/Sense/Current': None,
				'/DeviceInstance': 2,
				'/ProductId': 0x203}) # BMV
		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.battery.ttyO2': {
				'/Sense/Current': None
			}
		})

	@unittest.skip("Disabled for now")
	def test_sense_current_only_if_monitor(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/Sense/Current': None,
				'/DeviceInstance': 2,
				'/ProductId': 0xB00A}) # BYD

		# We prefer the Multi for SOC
		self._set_setting('/Settings/SystemSetup/BatteryService',
			'com.victronenergy.vebus/0')

		self._update_values(3000)
		self._check_external_values({
			'com.victronenergy.battery.ttyO2': {
				'/Sense/Current': None
			}
		})
