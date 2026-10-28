#!/usr/bin/env python3

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import StateOfPower

# Monkey patching for unit tests
import patches

class TestStateOfPower(TestSystemCalcBase):
	battery = 'com.victronenergy.battery.ttyO2'

	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.battery, product_name='battery', values={
			'/Dc/0/Voltage': 53.6,
			'/Dc/0/Current': 10.0,
			'/Soc': 80,
			'/InstalledCapacity': 100,
			'/Info/MaxChargeVoltage': 55.2,
			'/System/MaxCellVoltage': 3.37})
		self._update_values()

	def _set_current(self, current):
		# Cell with 2 mOhm resistance and no drift
		self._monitor.set_value(self.battery, '/Dc/0/Current', current)
		self._monitor.set_value(self.battery, '/System/MaxCellVoltage',
			round(3.35 + 0.002 * current, 3))

	def test_estimate(self):
		for t in range(600):
			self._set_current(10.0 if (t // 30) % 2 else 30.0)
			self._update_values()

		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/R'], 2.0, delta=0.1)
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/C'], 0.0, delta=1.0)
		# 3.48V reached at (3.48 - 3.35) / 0.002 = 65A
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/ChargeCurrent'], 65, delta=3)
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/ChargePower'], 65 * 53.6, delta=3 * 53.6)

	def test_pv_current(self):
		self._add_device('com.victronenergy.solarcharger.ttyO1', {
			'/Dc/0/Voltage': 53.6,
			'/Dc/0/Current': 20})
		for t in range(600):
			self._set_current(10.0 if (t // 30) % 2 else 30.0)
			self._update_values()

		# MPPTs take 20A of the 65A
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/ChargeCurrent'], 45, delta=3)
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/ChargePower'], 45 * 53.6, delta=3 * 53.6)

		# Never negative
		self._monitor.set_value('com.victronenergy.solarcharger.ttyO1', '/Dc/0/Current', 100)
		self._update_values(2000)
		self.assertEqual(self._service['/Dc/Battery/Sop/ChargeCurrent'], 0)
		self.assertEqual(self._service['/Dc/Battery/Sop/ChargePower'], 0)

	def test_capacity(self):
		self._set_current(10.0)
		self._update_values()
		self.assertEqual(StateOfPower.instance.capacity, 100)
		# Initial R is K_TYP / capacity = 0.9 mOhm, step is 0.005 / R
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/CurrentStep'], 5.6, delta=0.05)

		# Step is capped at 10% of capacity, tracked live
		self._monitor.set_value(self.battery, '/InstalledCapacity', 40)
		self._update_values(2000)
		self.assertEqual(StateOfPower.instance.capacity, 40)
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/CurrentStep'], 4.0, delta=0.05)

		# Unknown capacity falls back to the default of 100Ah
		self._monitor.set_value(self.battery, '/InstalledCapacity', None)
		self._update_values(2000)
		self.assertIsNone(StateOfPower.instance.capacity)
		self.assertAlmostEqual(self._service['/Dc/Battery/Sop/CurrentStep'], 5.6, delta=0.05)

	def test_no_battery(self):
		self._update_values()
		self.assertIsNotNone(self._service['/Dc/Battery/Sop/R'])
		self._remove_device(self.battery)
		self._update_values()
		self._check_values({
			'/Dc/Battery/Sop/R': None,
			'/Dc/Battery/Sop/C': None,
			'/Dc/Battery/Sop/ChargeCurrent': None})
