#!/usr/bin/env python3
import unittest

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import PvInverters

# Monkey patching for unit tests
import patches

# Sentinel used to seed the acsystem Ac-coupled PV paths, so we can tell the
# difference between "written" and "left untouched".
SENTINEL = -12345


class TestPvInverter(TestSystemCalcBase):
	acsystem = 'com.victronenergy.acsystem.sys0'

	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='Multi',
			values={
				'/Ac/ActiveIn/L1/P': 0,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 0,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': 0,
				'/DeviceInstance': 0,
				'/Soc': 50.0,
				'/State': 3,
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

	def _add_pvinverter(self, service='com.victronenergy.pvinverter.mock', l1=None, l2=None, l3=None):
		self._add_device(service, {
			'/Ac/L1/Power': l1,
			'/Ac/L2/Power': l2,
			'/Ac/L3/Power': l3,
			'/Position': 1,  # On output
			'/DeviceInstance': 20,
		})

	def _add_acsystem(self, service, instance=30):
		self._add_device(service, product_name='Multi RS',
			values={
				'/DeviceInstance': instance,
				'/Pv/L1/AcCoupledPower': SENTINEL,
				'/Pv/L2/AcCoupledPower': SENTINEL,
				'/Pv/L3/AcCoupledPower': SENTINEL,
			})

	def test_no_timer_without_acsystem(self):
		# Systems without a Multi-RS should not run the push timer at all.
		self._add_pvinverter(l1=500)
		self._update_values(5000)
		self.assertIsNone(PvInverters.instance._timer)

	def test_accoupled_pv_forwarded_to_acsystem(self):
		self._add_pvinverter(l1=500, l2=300)
		self._add_acsystem(self.acsystem)

		# Advance past the 5-second push timer.
		self._update_values(5000)

		self._check_external_values({
			self.acsystem: {
				'/Pv/L1/AcCoupledPower': 500,
				'/Pv/L2/AcCoupledPower': 300,
			}})

	def test_absent_phase_is_left_untouched(self):
		# No PV on L3, so /Ac/PvOnOutput/L3/Power stays None. We must not write
		# anything for that phase, letting the Multi-RS time out and assume none.
		self._add_pvinverter(l1=500)
		self._add_acsystem(self.acsystem)

		self._update_values(5000)

		self._check_external_values({
			self.acsystem: {
				'/Pv/L1/AcCoupledPower': 500,
				'/Pv/L2/AcCoupledPower': SENTINEL,
				'/Pv/L3/AcCoupledPower': SENTINEL,
			}})

	def test_forwarded_to_multiple_acsystems(self):
		self._add_pvinverter(l1=750)
		self._add_acsystem('com.victronenergy.acsystem.sys0', instance=30)
		self._add_acsystem('com.victronenergy.acsystem.sys1', instance=31)

		self._update_values(5000)

		self._check_external_values({
			'com.victronenergy.acsystem.sys0': {'/Pv/L1/AcCoupledPower': 750},
			'com.victronenergy.acsystem.sys1': {'/Pv/L1/AcCoupledPower': 750},
		})


if __name__ == '__main__':
	unittest.main()
