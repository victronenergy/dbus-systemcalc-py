#!/usr/bin/env python3
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
				'/Speed': 5,
				'/Fix': 0 })
		self._add_device('com.victronenergy.gps.ttyX2',
			product_name='ACME Gps',
			values={
				'/DeviceInstance': 1,
				'/Speed': 10,
				'/Fix': 0 })
		self._update_values()

	def test_nofix(self):
		self._check_values({
			'/GpsService': None
		})

	def test_use_lowest_deviceinstance(self):
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 1)
		self._monitor.set_value('com.victronenergy.gps.ttyX2', '/Fix', 1)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX1',
			'/GpsSpeed': 5
		})

		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 0)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX2',
			'/GpsSpeed': 10
		})

	def test_no_fix_invalidated(self):
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 1)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX1',
			'/GpsSpeed': 5
		})
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 0)
		self._update_values()
		self._check_values({
			'/GpsService': None,
			'/GpsSpeed': None
		})
