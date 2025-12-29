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

	def _addFirstMotorDrive(self):
		self._add_device('com.victronenergy.motordrive.ttyX1',
			product_name='ACME Motor Drive',
			values={
				'/Dc/0/Voltage': 24,
				'/Dc/0/Current': 10,
				'/Dc/0/Power': 240,
				'/Motor/RPM': 1000,
				'/DeviceInstance': 1 })

	def _removeFirstMotorDrive(self):
		self._remove_device('com.victronenergy.motordrive.ttyX1')

	def _addSecondMotorDrive(self):
		self._add_device('com.victronenergy.motordrive.ttyX2',
			product_name='ACME Motor Drive',
			values={
				'/Dc/0/Voltage': 48,
				'/Dc/0/Current': 10,
				'/Dc/0/Power': 480,
				'/Motor/RPM': 500,
				'/DeviceInstance': 2 })

	def _removeSecondMotorDrive(self):
		self._remove_device('com.victronenergy.motordrive.ttyX2')

	def test_enable_electric_propulsion_on_motordrive_detection(self):
		self._check_settings({
			'electricpropulsionenabled': 0,
		})

		self._addFirstMotorDrive()
		self._check_settings({
			'electricpropulsionenabled': 1,
		})

	def test_check_values(self):
		self._addFirstMotorDrive()
		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/Voltage': 24,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 240,
			'/MotorDrive/0/RPM': 1000
		})

		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Current', 5)
		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Power', None)

		self._update_values()
		self._check_values({
			'/MotorDrive/Power': 120
		})

	def test_power_inference_with_invalid_values(self):
		self._addFirstMotorDrive()
		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/Voltage': 24,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 240,
			'/MotorDrive/0/RPM': 1000
		})

		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Voltage', None)
		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Current', None)
		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Power', None)

		self._update_values()
		self._check_values({
			'/MotorDrive/Power': None
		})

	def test_dual_drive(self):
		self._addFirstMotorDrive()
		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/1/Service': None,
			'/MotorDrive/1/DeviceInstance': None,
			'/MotorDrive/Voltage': 24,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 240,
			'/MotorDrive/0/RPM': 1000,
			'/MotorDrive/1/RPM': None,
		})

		self._addSecondMotorDrive()
		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/1/Service': None,
			'/MotorDrive/1/DeviceInstance': None,
			'/MotorDrive/Voltage': 24,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 240,
			'/MotorDrive/0/RPM': 1000,
			'/MotorDrive/1/RPM': None,
		})

		self._set_setting('/Settings/Gui/ElectricPropulsionUI/DualDrive/Left/DeviceInstance', 1)
		self._set_setting('/Settings/Gui/ElectricPropulsionUI/DualDrive/Right/DeviceInstance', 2)

		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/1/Service': 'com.victronenergy.motordrive.ttyX2',
			'/MotorDrive/1/DeviceInstance': 2,
			'/MotorDrive/Voltage': 36,
			'/MotorDrive/Current': 20,
			'/MotorDrive/Power': 720,
			'/MotorDrive/0/RPM': 1000,
			'/MotorDrive/1/RPM': 500,
		})

		self._removeFirstMotorDrive()
		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX2',
			'/MotorDrive/0/DeviceInstance': 2,
			'/MotorDrive/1/Service': None,
			'/MotorDrive/1/DeviceInstance': None,
			'/MotorDrive/Voltage': 48,
			'/MotorDrive/Current': 10,
			'/MotorDrive/Power': 480,
			'/MotorDrive/0/RPM': 500,
			'/MotorDrive/1/RPM': None,
		})

	def test_dual_drive_power_inference(self):
		self._addFirstMotorDrive()
		self._addSecondMotorDrive()
		self._monitor.set_value('com.victronenergy.motordrive.ttyX1', '/Dc/0/Power', None)
		self._monitor.set_value('com.victronenergy.motordrive.ttyX2', '/Dc/0/Power', None)
		self._set_setting('/Settings/Gui/ElectricPropulsionUI/DualDrive/Left/DeviceInstance', 1)
		self._set_setting('/Settings/Gui/ElectricPropulsionUI/DualDrive/Right/DeviceInstance', 2)

		self._update_values()
		self._check_values({
			'/MotorDrive/0/Service': 'com.victronenergy.motordrive.ttyX1',
			'/MotorDrive/0/DeviceInstance': 1,
			'/MotorDrive/1/Service': 'com.victronenergy.motordrive.ttyX2',
			'/MotorDrive/1/DeviceInstance': 2,
			'/MotorDrive/Voltage': 36,
			'/MotorDrive/Current': 20,
			'/MotorDrive/Power': 720,
			'/MotorDrive/0/RPM': 1000,
			'/MotorDrive/1/RPM': 500,
		})