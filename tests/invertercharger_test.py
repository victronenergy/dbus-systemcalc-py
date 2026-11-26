import unittest
import context
import patches
from base import TestSystemCalcBase


class InverterCurrentFallbackTest(TestSystemCalcBase):
	def test_current_without_power(self):
		self._add_device('com.victronenergy.inverter.test', {
			'/Dc/0/Voltage': 48, '/Dc/0/Current': -5})
		self._update_values()
		self._check_values({'/Dc/InverterCharger/Current': -5,
			'/Dc/InverterCharger/Power': -240})

	def test_current_without_voltage_or_power(self):
		self._add_device('com.victronenergy.inverter.test', {
			'/Dc/0/Voltage': None, '/Dc/0/Current': -5})
		self._update_values()
		self._check_values({'/Dc/InverterCharger/Current': -5,
			'/Dc/InverterCharger/Power': None})

	def test_current_is_summed_across_devices(self):
		for i in range(2):
			self._add_device('com.victronenergy.inverter.test%d' % i, {
				'/DeviceInstance': i, '/Dc/0/Voltage': 48,
				'/Dc/0/Current': -5})
		self._update_values()
		self._check_values({'/Dc/InverterCharger/Current': -10,
			'/Dc/InverterCharger/Power': -480})
