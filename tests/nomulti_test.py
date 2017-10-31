#!/usr/bin/env python
import json
import logging
import math
import os
import sys
import tempfile
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
import dbus_systemcalc
from delegates import Hub1Bridge
import mock_gobject
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

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
		new_values = Hub1Bridge.distribute(actual_values, max_values, 3)
		self.assertEqual(new_values, [2, 3, 4])

	def test_hub1bridge_distr_2(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 9.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_3(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 10.0)
		self.assertEqual(new_values, [6, 5, 4])

	def test_hub1bridge_distr_4(self):
		actual_values = [1, 2, 3]
		max_values = [6, 5, 4]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [3.5, 4.5, 4])

	def test_hub1bridge_distr_5(self):
		actual_values = [3, 2, 1]
		max_values = [4, 5, 6]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [4, 4.5, 3.5])

	def test_hub1bridge_distr_6(self):
		actual_values = [4, 5, 6]
		max_values = [1, 2, 8]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 0.0)
		self.assertEqual(new_values, [1, 2, 8])

	def test_hub1bridge_distr_7(self):
		actual_values = [1]
		max_values = [5]
		new_values = Hub1Bridge.distribute(actual_values, max_values, 6.0)
		self.assertEqual(new_values, [5])
