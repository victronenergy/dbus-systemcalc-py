# our own packages
import context
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestAcInputDelegate(TestSystemCalcBase):
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
				'/Ac/NumberOfAcInputs': 2,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/Dc/0/Temperature': 24,
				'/DeviceInstance': 247,
				'/Dc/0/MaxChargeCurrent': 999,
				'/Soc': 53.2,
				'/State': 3,
			})
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

	def test_quattro_grid_and_generator(self):
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'vebus',
			'/Ac/In/1/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/1/DeviceInstance': 247,
			'/Ac/In/1/Connected': 0,
		})

		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._update_values()
		self._check_values({
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 0,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'vebus',
			'/Ac/In/1/DeviceInstance': 247,
			'/Ac/In/1/Connected': 1,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 2,
		})

	def test_gridmeter_but_no_genset_meter(self):
		self._add_device('com.victronenergy.grid.ttyUSB0', {
			'/Ac/L1/Power': 1230,
			'/DeviceInstance': 30,
		})

		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/ServiceName': 'com.victronenergy.grid.ttyUSB0',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'vebus',
			'/Ac/In/1/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/1/DeviceInstance': 247,
			'/Ac/In/1/Connected': 0,
		})

		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._update_values()
		self._check_values({
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 0,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'vebus',
			'/Ac/In/1/DeviceInstance': 247,
			'/Ac/In/1/Connected': 1,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 2,
		})

	def test_genset_meter_but_no_gridmeter(self):
		self._add_device('com.victronenergy.genset.ttyUSB0', {
			'/Ac/L1/Power': 1230,
			'/DeviceInstance': 30,
		})

		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'genset',
			'/Ac/In/1/ServiceName': 'com.victronenergy.genset.ttyUSB0',
			'/Ac/In/1/DeviceInstance': 30,
			'/Ac/In/1/Connected': 0,
		})

		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._update_values()
		self._check_values({
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 0,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'genset',
			'/Ac/In/1/DeviceInstance': 30,
			'/Ac/In/1/Connected': 1,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 2,
		})

	def test_genset_meter_and_gridmeter(self):
		self._add_device('com.victronenergy.grid.ttyUSB0', {
			'/Ac/L1/Power': 1230,
			'/DeviceInstance': 30,
		})
		self._add_device('com.victronenergy.genset.ttyUSB1', {
			'/Ac/L1/Power': 1231,
			'/DeviceInstance': 31,
		})

		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/ServiceName': 'com.victronenergy.grid.ttyUSB0',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'genset',
			'/Ac/In/1/ServiceName': 'com.victronenergy.genset.ttyUSB1',
			'/Ac/In/1/DeviceInstance': 31,
			'/Ac/In/1/Connected': 0,
		})

		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._update_values()
		self._check_values({
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 0,

			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'genset',
			'/Ac/In/1/DeviceInstance': 31,
			'/Ac/In/1/Connected': 1,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 2,
		})

	def test_only_one_input(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/NumberOfAcInputs', 1)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': None,
			'/Ac/In/1/ServiceType': None,
			'/Ac/In/1/ServiceName': None,
			'/Ac/In/1/DeviceInstance': None,
			'/Ac/In/1/Connected': None,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': None,
		})

	def test_two_inputs_but_second_unused(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput2', 0)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': None,
			'/Ac/In/1/ServiceType': None,
			'/Ac/In/1/ServiceName': None,
			'/Ac/In/1/DeviceInstance': None,
			'/Ac/In/1/Connected': None,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 0,
		})

	def test_two_inputs_but_first_unused(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 0)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			'/Ac/In/0/Source': 2,
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/Connected': 1,

			'/Ac/In/1/Source': None,
			'/Ac/In/1/ServiceType': None,
			'/Ac/In/1/ServiceName': None,
			'/Ac/In/1/DeviceInstance': None,
			'/Ac/In/1/Connected': None,
		})

		# The compaction above moved input 2 onto /Ac/In/0/. These keep
		# the input number.
		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 0,
			'/Ac/SystemSetup/In/2/Type': 2,
		})

	def test_zero_inputs(self):
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/NumberOfAcInputs', 0)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 0,

			'/Ac/In/0/Source': None,
			'/Ac/In/0/ServiceType': None,
			'/Ac/In/0/ServiceName': None,
			'/Ac/In/0/DeviceInstance': None,
			'/Ac/In/0/Connected': None,

			'/Ac/In/1/Source': None,
			'/Ac/In/1/ServiceType': None,
			'/Ac/In/1/ServiceName': None,
			'/Ac/In/1/DeviceInstance': None,
			'/Ac/In/1/Connected': None,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': None,
			'/Ac/SystemSetup/In/2/Type': None,
		})

	def test_all_inputs_unused(self):
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 0)
		self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput2', 0)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 0,

			'/Ac/In/0/Source': None,
			'/Ac/In/0/ServiceType': None,
			'/Ac/In/0/ServiceName': None,
			'/Ac/In/0/DeviceInstance': None,
			'/Ac/In/0/Connected': None,

			'/Ac/In/1/Source': None,
			'/Ac/In/1/ServiceType': None,
			'/Ac/In/1/ServiceName': None,
			'/Ac/In/1/DeviceInstance': None,
			'/Ac/In/1/Connected': None,
		})

		self._check_values({
			'/Ac/SystemSetup/In/1/Type': 0,
			'/Ac/SystemSetup/In/2/Type': 0,
		})

	def test_vrm_di_zero_on_ccgx(self):
		self._update_values()
		self._check_values({
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO1',
			'/Ac/In/0/DeviceInstance': 247,
			'/Ac/In/0/VrmDeviceInstance': 0,
			'/Ac/In/0/Connected': 1,
		})

		# Put the Multi on a different port, eg Venus-GX has it on ttyO5
		self._remove_device('com.victronenergy.vebus.ttyO1')
		self._add_device('com.victronenergy.vebus.ttyO5',
			product_name='Multi',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Ac/NumberOfAcInputs': 2,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': -8,
				'/Dc/0/Temperature': 24,
				'/DeviceInstance': 261,
				'/Dc/0/MaxChargeCurrent': 999,
				'/Soc': 53.2,
				'/State': 3,
			})

		self._update_values()
		self._check_values({
			'/Ac/In/0/ServiceType': 'vebus',
			'/Ac/In/0/ServiceName': 'com.victronenergy.vebus.ttyO5',
			'/Ac/In/0/DeviceInstance': 261,
			'/Ac/In/0/VrmDeviceInstance': 261,
			'/Ac/In/0/Connected': 1,
		})


class TestAcInputMultiRs(TestSystemCalcBase):
	""" A Multi-RS keeps its AC input configuration on the device itself,
	    rather than in the settings. """
	acsystem = 'com.victronenergy.acsystem.sys0'

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.acsystem,
			product_name='Multi RS',
			values={
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/NumberOfAcInputs': 2,
				'/Ac/In/1/Type': 2,
				'/Ac/In/2/Type': 1,
				'/DeviceInstance': 30,
			})
		# The other way around from the device, so it is obvious which one
		# is being used.
		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

	def test_types_come_from_the_device(self):
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 2,
			'/Ac/In/1/Source': 1,

			'/Ac/SystemSetup/In/1/Type': 2,
			'/Ac/SystemSetup/In/2/Type': 1,
		})

	def test_first_input_unused(self):
		self._monitor.set_value(self.acsystem, '/Ac/In/1/Type', 0)
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			# Compacted: input 2 moved down onto /Ac/In/0/
			'/Ac/In/0/Source': 1,
			'/Ac/In/1/Source': None,

			'/Ac/SystemSetup/In/1/Type': 0,
			'/Ac/SystemSetup/In/2/Type': 1,
		})


class TestAcInputNoInverterCharger(TestSystemCalcBase):
	""" Without an inverter/charger there is no AC input configuration at
	    all. The meters that are on the bus are used instead. """
	def _add_grid_meter(self):
		self._add_device('com.victronenergy.grid.ttyUSB0', {
			'/Ac/L1/Power': 1230,
			'/DeviceInstance': 30,
		})

	def _add_genset_meter(self):
		self._add_device('com.victronenergy.genset.ttyUSB1', {
			'/Ac/L1/Power': 1231,
			'/DeviceInstance': 31,
		})

	def test_no_meters(self):
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 0,

			'/Ac/SystemSetup/In/1/Type': None,
			'/Ac/SystemSetup/In/2/Type': None,
		})

	def test_grid_meter_only(self):
		self._add_grid_meter()
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/Connected': 1,

			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': None,
		})

	def test_genset_meter_only(self):
		self._add_genset_meter()
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			# A lone genset meter still takes the first slot, but it is a
			# genset, not the grid.
			'/Ac/In/0/Source': 2,
			'/Ac/In/0/ServiceType': 'genset',
			'/Ac/In/0/Connected': 1,

			'/Ac/SystemSetup/In/1/Type': 2,
			'/Ac/SystemSetup/In/2/Type': None,
		})

	def test_grid_and_genset_meter(self):
		self._add_grid_meter()
		self._add_genset_meter()
		self._update_values()
		self._check_values({
			'/Ac/In/NumberOfAcInputs': 2,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/1/Source': 2,
			'/Ac/In/1/ServiceType': 'genset',

			'/Ac/SystemSetup/In/1/Type': 1,
			'/Ac/SystemSetup/In/2/Type': 2,
		})
