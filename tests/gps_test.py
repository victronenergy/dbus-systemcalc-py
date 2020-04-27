#!/usr/bin/env python
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import BatterySense, Gps

# Monkey patching for unit tests
import patches

class GpsTest(TestSystemCalcBase):

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.gps.ttyX1',
			product_name='ACME Gps',
			values={
				'/DeviceInstance': 0,
				'/Position/Latitude': 1.234,
				'/Position/Longitude': 2.345,
				'/Course': 1,
				'/Speed': 5,
				'/Altitude': 100,
				'/Fix': 0 })
		self._add_device('com.victronenergy.gps.ttyX2',
			product_name='ACME Gps',
			values={
				'/DeviceInstance': 1,
				'/Position/Latitude': 3.234,
				'/Position/Longitude': 4.345,
				'/Course': 11,
				'/Speed': 55,
				'/Altitude': 200,
				'/Fix': 0 })

	def test_nofix(self):
		self._check_values({
			'/Gps/Position/Latitude': None,
			'/Gps/Position/Longitude': None,
			'/Gps/Course': None,
			'/Gps/Speed': None,
			'/Gps/Altitude': None,
		})

	def test_use_lowest_deviceinstance(self):
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 1)
		self._monitor.set_value('com.victronenergy.gps.ttyX2', '/Fix', 1)
		self._check_values({
			'/Gps/Position/Latitude': 1.234,
			'/Gps/Position/Longitude': 2.345,
			'/Gps/Course': 1,
			'/Gps/Speed': 5,
			'/Gps/Altitude': 100,
		})

		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 0)
		self._check_values({
			'/Gps/Position/Latitude': 3.234,
			'/Gps/Position/Longitude': 4.345,
			'/Gps/Course': 11,
			'/Gps/Speed': 55,
			'/Gps/Altitude': 200,
		})
