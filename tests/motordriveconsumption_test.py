#!/usr/bin/env python3
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import MotorDriveConsumption

# Monkey patching for unit tests
import patches

import math


class MotorDriveConsumptionTest(TestSystemCalcBase):

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._motordriveconsumption = next(
			x
			for x in self._system_calc._modules
			if isinstance(x, MotorDriveConsumption)
		)

	def _addMotorDrive(self):
		self._add_device(
			"com.victronenergy.motordrive.ttyX1",
			product_name="ACME Motor Drive",
			values={
				"/Dc/0/Voltage": 24,
				"/Dc/0/Current": 10,
				"/Dc/0/Power": 240,
				"/Motor/RPM": 1000,
				"/DeviceInstance": 1,
			},
		)

	def _removeMotorDrive(self):
		self._remove_device("com.victronenergy.motordrive.ttyX1")

	def _addGps(self):
		self._add_device(
			"com.victronenergy.gps.ttyX2",
			product_name="ACME Gps",
			values={
				"/DeviceInstance": 2,
				"/Speed": 0,
				"/UtcTime": 0,
				"/Position/Latitude": 0.0,
				"/Position/Longitude": 0.0,
				"/Fix": 1,
			},
		)

	def _removeGps(self):
		self._remove_device("com.victronenergy.gps.ttyX2")

	def destination_point(self, lat, lon, distance, bearing):
		R = 6371000
		lat1 = math.radians(lat)
		lon1 = math.radians(lon)
		bearing = math.radians(bearing)
		delta = distance / R
		lat2 = math.asin(
			math.sin(lat1) * math.cos(delta)
			+ math.cos(lat1) * math.sin(delta) * math.cos(bearing)
		)
		lon2 = lon1 + math.atan2(
			math.sin(bearing) * math.sin(delta) * math.cos(lat1),
			math.cos(delta) - math.sin(lat1) * math.sin(lat2),
		)
		lat2 = math.degrees(lat2)
		lon2 = math.degrees(lon2)
		return lat2, lon2

	def simulate_steps(self, steps):
		last_latitude = 0.0
		last_longitude = 0.0
		last_time = 0

		# t = 0s
		self._monitor.set_value("com.victronenergy.gps.ttyX2", "/UtcTime", 0)
		self._monitor.set_value(
			"com.victronenergy.gps.ttyX2", "/Position/Latitude", last_latitude
		)
		self._monitor.set_value(
			"com.victronenergy.gps.ttyX2", "/Position/Longitude", last_longitude
		)
		self._service["/MotorDrive/Power"] = None
		self._service["/MotorDrive/Current"] = None
		self._update_values()

		for step in steps:
			time_step = step[0]
			distance = step[1]
			bearing = step[2]
			power = step[3]
			current = step[4]

			# t + time_step s
			new_latitude, new_longitude = self.destination_point(
				last_latitude, last_longitude, distance, bearing
			)
			new_time = last_time + time_step

			self._monitor.set_value("com.victronenergy.gps.ttyX2", "/UtcTime", new_time)
			self._monitor.set_value(
				"com.victronenergy.gps.ttyX2", "/Position/Latitude", new_latitude
			)
			self._monitor.set_value(
				"com.victronenergy.gps.ttyX2", "/Position/Longitude", new_longitude
			)
			self._service["/MotorDrive/Power"] = power
			self._service["/MotorDrive/Current"] = current
			self._update_values()

			last_latitude = new_latitude
			last_longitude = new_longitude
			last_time = new_time

	def test_basic(self):
		self._addGps()
		self._addMotorDrive()
		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)

		# MockDbusService does not update values in DbusMonitor automatically
		# so we need to set it manually
		self._monitor.set_value(
			"com.victronenergy.system", "/GpsService", "com.victronenergy.gps.ttyX2"
		)

		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)
		self.simulate_steps(
			[
				# travelling 1km at 1000W (21A), following a 45 degree bearing
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
			]
		)
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": 166.66666666666663,  # Wh/km
				"/MotorDrive/ConsumptionAh": 3.5000000000000004,  # Ah/km
			}
		)

	def test_too_slow(self):
		self._addGps()
		self._addMotorDrive()
		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)

		# MockDbusService does not update values in DbusMonitor automatically
		# so we need to set it manually
		self._monitor.set_value(
			"com.victronenergy.system", "/GpsService", "com.victronenergy.gps.ttyX2"
		)

		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)
		self.simulate_steps(
			[
				# travelling 1km at 1000W (21A), following a 45 degree bearing
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[1000, 0.2, 0, 1000, 21], # below minimum speed, should be ignored
				[1000, 0.2, 0, 1000, 21], # below minimum speed, should be ignored
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
			]
		)
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": 166.66666666666663,  # Wh/km
				"/MotorDrive/ConsumptionAh": 3.499999999999999,  # Ah/km
			}
		)

	def test_too_fast(self):
		self._addGps()
		self._addMotorDrive()
		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)

		# MockDbusService does not update values in DbusMonitor automatically
		# so we need to set it manually
		self._monitor.set_value(
			"com.victronenergy.system", "/GpsService", "com.victronenergy.gps.ttyX2"
		)

		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)
		self.simulate_steps(
			[
				# travelling 1km at 1000W (21A), following a 45 degree bearing
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 1300, 45, 20000, 420], # above maximum speed, should be clamped
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
			]
		)
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": 239.09107369183417,  # Wh/km
				"/MotorDrive/ConsumptionAh": 5.020912547528517,  # Ah/km
			}
		)

	def test_bearing_turn_too_quick(self):
		self._addGps()
		self._addMotorDrive()
		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
			}
		)

		# MockDbusService does not update values in DbusMonitor automatically
		# so we need to set it manually
		self._monitor.set_value(
			"com.victronenergy.system", "/GpsService", "com.victronenergy.gps.ttyX2"
		)

		self._update_values()
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": None,
				"/MotorDrive/ConsumptionAh": None,
			}
		)
		self.simulate_steps(
			[
				# travelling 1km at 1000W (21A), following a 45 degree bearing
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[1000, 10, 200, 1000, 21], # above bearing change threshold, should be ignored
				[1000, 10, 0, 1000, 21], # above bearing change threshold, should be ignored
				[10000, 10, 0, 1000, 21], # above bearing change threshold, delta time large enough to reset
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
				[60000, 100, 45, 1000, 21],
			]
		)
		self._check_values(
			{
				"/GpsService": "com.victronenergy.gps.ttyX2",
				"/MotorDrive/ConsumptionWh": 166.66666666666663,  # Wh/km
				"/MotorDrive/ConsumptionAh": 3.4999999999999996,  # Ah/km
			}
		)
