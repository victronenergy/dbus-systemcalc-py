#!/usr/bin/env python3
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import Gps

# Monkey patching for unit tests
import patches

# Testing tools
from mock_gobject import timer_manager

# Time travel patch
Gps._get_time = lambda *a: timer_manager.datetime

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

	def test_speed_smoothing(self):
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 1)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX1',
			'/GpsSpeed': 5
		})

		data = [
			(6, 5.632120558828557),
			(7, 6.496785275591945),
			(8, 7.44699820722408),
			(9, 8.428682568335347),
			(10, 9.421944621336262),
			(11, 10.419465869159595),
			(12, 11.418553987194041),
			(13, 12.418218524566138),
			(14, 13.418095114762053),
			(15, 14.41804971483229),
			(15, 14.785912454302942),
			(15, 14.921241593327201),
			(15, 14.971026401365659),
			(15, 14.989341208725673),
			(15, 14.996078849822439),
			(14, 14.366436930635373),
		]

		for speed, expected_ema in data:
			self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Speed', speed)
			self._update_values()
			self._check_values({
				'/GpsService': 'com.victronenergy.gps.ttyX1',
				'/GpsSpeed': expected_ema
			})

	def test_no_speed(self):
		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Fix', 1)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX1',
			'/GpsSpeed': 5
		})

		self._monitor.set_value('com.victronenergy.gps.ttyX1', '/Speed', None)
		self._update_values()
		self._check_values({
			'/GpsService': 'com.victronenergy.gps.ttyX1',
			'/GpsSpeed': None
		})
