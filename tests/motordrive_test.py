#!/usr/bin/env python3
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import MotorDrive

# Monkey patching for unit tests
import patches

class MotorDriveTest(TestSystemCalcBase):

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.motordrive.ttyX1',
			product_name='ACME Motor Drive',
			values={
				'/Dc/0/Voltage': 24,
				'/Dc/0/Current': 10,
				'/Dc/0/Power': 240,
				'/Motor/RPM': 1000 })
		self._update_values()

	def test_check_values(self):
		self._check_values({
			'/MotorDrive/Voltage': 24,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 240,
			'/MotorDrive/RPM': 1000
		})

		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Current', 5)
		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Power', None)

		self._update_values()
		self._check_values({
			'/MotorDrive/Power': 120
		})