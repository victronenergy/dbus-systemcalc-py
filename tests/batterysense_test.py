#!/usr/bin/env python
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import BatterySense, Dvcc

# Monkey patching for unit tests
import patches


class VoltageSenseTest(TestSystemCalcBase):
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
				'/Dc/0/Temperature': None,
				'/DeviceInstance': 0,
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),  # Hub-4 assistant
				'/Dc/0/MaxChargeCurrent': None,
				'/Soc': 53.2,
				'/State': 3,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
				'/BatterySense/Voltage': None,
				'/BatterySense/Temperature': None,
				'/FirmwareFeatures/BolFrame': 1,
				'/FirmwareFeatures/BolUBatAndTBatSense': 1,
				'/Hub4/AssistantId': None
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

	def test_voltage_sense_no_battery_monitor_old_vebus_firmware(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/FirmwareFeatures/BolUBatAndTBatSense', 0)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.32,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		self._check_values({
			'/Dc/Battery/Voltage': 12.25,
			'/Dc/Battery/VoltageService': 'com.victronenergy.vebus.ttyO1'
		})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': None},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': None}})

	def test_voltage_sense_no_battery_monitor_old_mppt_firmware(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Voltage', None)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Dc/0/Voltage': 12.32,
			'/Dc/0/Current': 9.7,
			'/Link/NetworkMode': 5,
			'/Link/VoltageSense': None},
			connection='VE.Direct')
		self._update_values(5000)
		self._check_values({
			'/Dc/Battery/Voltage': 12.25,
			'/Dc/Battery/VoltageService': 'com.victronenergy.vebus.ttyO1'
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.25}})

	def test_voltage_sense_no_battery_monitor(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Voltage', None)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		self._check_values({
			'/Dc/Battery/Voltage': 12.25,
			'/Dc/Battery/VoltageService': 'com.victronenergy.vebus.ttyO1'
		})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': None},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.25}})

	def test_sense_mppt_and_battery_monitor(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Voltage', None)
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		self._check_values({
			'/Dc/Battery/Voltage': 12.15,
			'/Dc/Battery/Temperature': 25,
			'/Dc/Battery/VoltageService': 'com.victronenergy.battery.ttyO2'
		})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': 12.15},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.15}})

		# Temperature is slower
		self._update_values(13000)
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Temperature': 25},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': 25}})

	def test_voltage_sense_vebus_and_battery_monitor(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Voltage', None)
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._update_values(5000)
		self._check_values({
			'/Control/SolarChargerVoltageSense': 0, # No solarchargers
			'/Control/BatteryVoltageSense': 1,
			'/Dc/Battery/Voltage': 12.15,
			'/Dc/Battery/VoltageService': 'com.victronenergy.battery.ttyO2'
		})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': 12.15}})

	def test_voltage_sense_disabled(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/BatterySense/Voltage', None)
		self._set_setting('/Settings/SystemSetup/SharedVoltageSense', 0)

		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		# Check that voltagesense is indicated as inactive
		self._check_values({
			'/Control/SolarChargerVoltageSense': 0,
			'/Control/BatteryVoltageSense': 0
		})
		# Check that other devices were left alone
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': None}})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/SenseVoltage': None}})

	def test_temp_sense_disabled(self):
		self._set_setting('/Settings/Services/Bol', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/BatterySense/Voltage', None)
		self._set_setting('/Settings/SystemSetup/SharedTemperatureSense', 0)

		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 27,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		# Check that tempsense is indicated as inactive
		self._check_values({
			'/Control/SolarChargerTemperatureSense': 0,
		})
		# Check that other devices were left alone
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Temperature': None}})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': None}})

	def test_no_dvcc_no_sense(self):
		self._set_setting('/Settings/Services/Bol', 0)
		self._set_setting('/Settings/SystemSetup/SharedVoltageSense', 1)
		self._set_setting('/Settings/SystemSetup/SharedTemperatureSense', 1)

		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/FirmwareFeatures/BolUBatAndTBatSense', 1)
		self._monitor.add_value('com.victronenergy.vebus.ttyO1',
			'/BatterySense/Voltage', None)

		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 26,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)
		# Check that voltagesense is indicated as inactive
		self._check_values({
			'/Control/SolarChargerVoltageSense': 0,
			'/Control/BatteryVoltageSense': 0
		})
		# Check that other devices were left alone
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': None,
				'/BatterySense/Temperature': None}})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/SenseVoltage': None,
				'/Link/TemperatureSense': None}})

	def test_shared_temperature_sense(self):
		self._set_setting('/Settings/Services/Bol', 1)

		# This solarcharger has no temperature sensor
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7,
			'/Dc/0/Temperature': None},
			connection='VE.Direct')
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': None,
			'/Dc/Battery/TemperatureService': None,
			'/AutoSelectedTemperatureService': None
		})

		# If the battery has temperature, use it
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 8,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': 8,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.battery.ttyO2',
			'/AutoSelectedTemperatureService': 'battery on dummy'
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': 8}})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Temperature': 8}})


	def test_temperature_sense_order(self):
		self._set_setting('/Settings/Services/Bol', 1)

		# This solarcharger has no temperature sensor
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7,
			'/Dc/0/Temperature': None},
			connection='VE.Direct')

		# A temperature sensor of the wrong kind is not used
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'com.victronenergy.temperature/4/Temperature')
		self._add_device('com.victronenergy.temperature.ttyO4',
			product_name='temperature sensor',
			values={
				'/Temperature': -9,
				'/TemperatureType': 1,
				'/DeviceInstance': 4})
		self._update_values(3000)
		self._check_values({
			'/Dc/Battery/Temperature': None,
			'/Dc/Battery/TemperatureService': None,
			'/AutoSelectedTemperatureService': None
		})

		# The right kind is used.
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'com.victronenergy.temperature/3/Temperature')
		self._add_device('com.victronenergy.temperature.ttyO3',
			product_name='temperature sensor',
			values={
				'/Temperature': 9,
				'/TemperatureType': 0,
				'/DeviceInstance': 3})
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': 9,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.temperature.ttyO3',
			'/AutoSelectedTemperatureService': 'temperature sensor on dummy'
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': 9}})

		# Multi as temp sense
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'com.victronenergy.vebus/0/Dc/0/Temperature')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Temperature', 7)
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Temperature', None)
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': 7,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.vebus.ttyO1',
			'/AutoSelectedTemperatureService': 'Multi on dummy'
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': 7}})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Temperature': None}})

		# Battery as temp sense. First check that battery is used as default.
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 8,
				'/Soc': 15.3,
				'/DeviceInstance': 2})
		for selected in ('default', 'com.victronenergy.battery/2/Dc/0/Temperature'):
			self._set_setting('/Settings/SystemSetup/TemperatureService', selected)
			self._update_values(9000)
			self._check_values({
				'/Dc/Battery/Temperature': 8,
				'/Dc/Battery/TemperatureService': 'com.victronenergy.battery.ttyO2',
				'/AutoSelectedTemperatureService': 'battery on dummy'
			})
			self._check_external_values({
				'com.victronenergy.solarcharger.ttyO1': {
					'/Link/TemperatureSense': 8}})
			self._check_external_values({
				'com.victronenergy.vebus.ttyO1': {
					'/BatterySense/Temperature': 8}})

		# No sense
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'nosensor')
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': None,
			'/Dc/Battery/TemperatureService': None,
			'/AutoSelectedTemperatureService': None
		})

		# If Multi is battery service and it has a temp sensor, use it
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'default')
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/BatterySense/Temperature', None)
		self._update_values(9000)
		self._check_values({
			'/Dc/Battery/Temperature': 7,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.vebus.ttyO1',
			'/AutoSelectedTemperatureService': 'Multi on dummy'
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/TemperatureSense': 7}})
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Temperature': None}})

	def test_distribute_current_from_battery(self):
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 8,
				'/Soc': 15.3,
				'/Info/MaxChargeVoltage': 14.5,
				'/DeviceInstance': 2})

		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/FirmwareVersion': 0x0142,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Link/BatteryCurrent': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7,
			'/Dc/0/Temperature': None},
			connection='VE.Direct')

		self._add_device('com.victronenergy.vecan.can0', {
			'/Link/ChargeVoltage': None,
			'/Link/NetworkMode': None,
			'/Link/TemperatureSense': None,
			'/Link/VoltageSense': None,
			'/Link/BatteryCurrent': None})

		# DVCC is off
		self._set_setting('/Settings/Services/Bol', 0)
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 0 # disabled
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/BatteryCurrent': None}})

		# DVCC is on but BatteryCurrentSense is off
		self._set_setting('/Settings/Services/Bol', 1)
		self._set_setting('/Settings/SystemSetup/BatteryCurrentSense', 0)
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 0 # disabled
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/BatteryCurrent': None}})

		# BatteryCurrentSense is on, but the battery is in control
		self._set_setting('/Settings/SystemSetup/BatteryCurrentSense', 1)
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 1 # disabled, Ext. control
		})

		# Battery is dumb
		self._monitor.set_value('com.victronenergy.battery.ttyO2',
			'/Info/MaxChargeVoltage', None)
		self._update_values(6000) # Order of execution causes this to take a bit longer.
		self._check_values({
			'/Control/BatteryCurrentSense': 4 # enabled
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/BatteryCurrent': 5.3},
			'com.victronenergy.vecan.can0': {
				'/Link/BatteryCurrent': 5.3}})

		# ESS assistant installed
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._monitor.set_value('com.victronenergy.solarcharger.ttyO1', '/Link/BatteryCurrent', None)
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 1 # disabled on account of ESS
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/BatteryCurrent': None}})

		# Remove solar charger, ESS
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', None)
		self._remove_device('com.victronenergy.solarcharger.ttyO1')
		self._remove_device('com.victronenergy.vecan.can0')
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 2 # no chargers
		})

	def test_distribute_current_not_vebus(self):
		# Explicitly select Multi as battery service
		self._set_setting('/Settings/SystemSetup/BatteryService', 'com.victronenergy.vebus/0')
		self._set_setting('/Settings/Services/Bol', 1)
		self._set_setting('/Settings/SystemSetup/BatteryCurrentSense', 1)
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/FirmwareVersion': 0x0142,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Link/BatteryCurrent': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7,
			'/Dc/0/Temperature': None},
			connection='VE.Direct')
		self._update_values(3000)
		self._check_values({
			'/Control/BatteryCurrentSense': 3 # No suitable monitor
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/BatteryCurrent': None}})

	def test_ess_uses_multi_voltage(self):
		# DVCC is on
		self._set_setting('/Settings/Services/Bol', 1)

		# A battery
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2})

		# Solar charger
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)

		# Solar charger and multi both sync with the battery
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': 12.15},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.15}})

		# ESS assistant installed
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)
		self._update_values(5000)

		# Now the multi should be synced with the battery, but the solar
		# chargers should be synced with the Multi
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': 12.15},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.25}})

	def test_ess_uses_multi_voltage_no_svs(self):
		# DVCC is on
		self._set_setting('/Settings/Services/Bol', 1)
		self._set_setting('/Settings/SystemSetup/SharedVoltageSense', 0)

		# ESS assistant installed
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Hub4/AssistantId', 5)

		# A battery
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 15.3,
				'/DeviceInstance': 2})

		# Solar charger
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 12.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')
		self._update_values(5000)

		# The solar chargers should be synced with the Multi. Multi
		# is not synced since SVS is off.
		self._check_external_values({
			'com.victronenergy.vebus.ttyO1': {
				'/BatterySense/Voltage': None},
			'com.victronenergy.solarcharger.ttyO1': {
				'/Link/VoltageSense': 12.25}})

	def test_forced_settings(self):
		self._set_setting('/Settings/Services/Bol', 0)
		self._set_setting('/Settings/SystemSetup/SharedVoltageSense', 1)

		# BYD, FreedomWON, Discover AES, BlueNova, BSL-BATT, Lynx Smart
		for product_id in (0xB00A, 0xB014, 0xB015, 0xB016, 0xB019, 0xB020, 0xB021, 0xA3E5, 0xA3E6):
			self._add_device('com.victronenergy.battery.ttyO2',
				product_name='battery',
				values={
					'/Dc/0/Voltage': 12.15,
					'/Dc/0/Current': 5.3,
					'/Dc/0/Power': 65,
					'/Soc': 50,
					'/DeviceInstance': 0,
					'/ProductId': product_id})
			self._update_values()
			self._check_settings({
				'vsense': 2, # Forced OFF
				'tsense': 2, # Forced OFF
				'bol': 3 # Forced ON
			})
			self.assertFalse(BatterySense.instance.has_vsense)
			self.assertTrue(Dvcc.instance.has_dvcc)
			self._remove_device('com.victronenergy.battery.ttyO2')


		# Battery with no forced settings (Pylontech used here)
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Soc': 50,
				'/DeviceInstance': 0,
				'/ProductId': 0xB009})

		self._update_values()
		self._check_settings({
			'vsense': 0, # Remains off, no longer forced
			'bol': 1 # Remains on, no longer forced
		})
		self.assertFalse(BatterySense.instance.has_vsense)
		self.assertTrue(Dvcc.instance.has_dvcc)

	def test_voltage_sense_inverter_and_battery_monitor(self):
		self._remove_device('com.victronenergy.vebus.ttyO1')
		self._set_setting('/Settings/Services/Bol', 1)
		self._set_setting('/Settings/SystemSetup/SharedVoltageSense', 1)
		self._set_setting('/Settings/SystemSetup/SharedTemperatureSense', 1)
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'com.victronenergy.temperature/3/Temperature')

		self._add_device('com.victronenergy.inverter.ttyO1', {
			'/Ac/Out/L1/P': -60,
			'/Ac/Out/L1/V': 234.2,
			'/Dc/0/Voltage': 53.1,
			'/Dc/0/Current': -1.2,
			'/DeviceInstance': 278,
			'/Soc': 53.2,
			'/State': 9,
			'/IsInverterCharger': 1,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Settings/ChargeCurrentLimit': 100},
			product_name='Inverter RS', connection='VE.Direct')

		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 53.25,
				'/Dc/0/Current': -1.25,
				'/Dc/0/Power': -65,
				'/Soc': 15.3,
				'/DeviceInstance': 2})

		self._add_device('com.victronenergy.temperature.ttyO3',
			product_name='temperature sensor',
			values={
				'/Temperature': 9.0,
				'/TemperatureType': 0,
				'/DeviceInstance': 3})

		self._update_values(12000)

		self._check_values({
			'/Control/SolarChargerVoltageSense': 1, # Inverter RS has a solarcharger
			'/Control/BatteryVoltageSense': 0,
			'/Dc/Battery/Voltage': 53.25,
			'/Dc/Battery/VoltageService': 'com.victronenergy.battery.ttyO2',
			'/Dc/Battery/Temperature': 9.0,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.temperature.ttyO3',
		})
		self._check_external_values({
			'com.victronenergy.inverter.ttyO1': {
				'/Link/VoltageSense': 53.25,
				'/Link/TemperatureSense': 9.0}})

	def test_inverter_is_tsense_and_vsense(self):
		self._remove_device('com.victronenergy.vebus.ttyO1')
		self._set_setting('/Settings/Services/Bol', 1)
		self._set_setting('/Settings/SystemSetup/SharedTemperatureSense', 1)
		self._set_setting('/Settings/SystemSetup/TemperatureService', 'com.victronenergy.inverter/278/Dc/0/Temperature')

		self._add_device('com.victronenergy.inverter.ttyO1', {
			'/Ac/Out/L1/P': -60,
			'/Ac/Out/L1/V': 234.2,
			'/Dc/0/Voltage': 53.1,
			'/Dc/0/Current': -1.2,
			'/Dc/0/Temperature': 24.5,
			'/DeviceInstance': 278,
			'/Soc': 53.2,
			'/State': 9,
			'/IsInverterCharger': 1,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/ChargeCurrent': None,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Settings/ChargeCurrentLimit': 100},
			product_name='Inverter RS', connection='VE.Direct')

		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 0,
			'/Link/NetworkMode': 0,
			'/Link/VoltageSense': None,
			'/Link/TemperatureSense': None,
			'/Dc/0/Voltage': 53.2,
			'/Dc/0/Current': 9.7},
			connection='VE.Direct')

		self._update_values(12000)

		self._check_values({
			'/Dc/Battery/TemperatureService': 'com.victronenergy.inverter.ttyO1',
			'/Dc/Battery/Voltage': 53.1,
			'/Dc/Battery/VoltageService': 'com.victronenergy.inverter.ttyO1',
			'/Dc/Battery/Temperature': 24.5,
			'/Dc/Battery/TemperatureService': 'com.victronenergy.inverter.ttyO1',
		})
		self._check_external_values({
			'com.victronenergy.solarcharger.ttyO2': {
				'/Link/VoltageSense': 53.1,
				'/Link/TemperatureSense': 24.5}})
