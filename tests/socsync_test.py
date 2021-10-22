import context
from base import TestSystemCalcBase
import patches

class TestSocSync(TestSystemCalcBase):
	def setUp(self):
		TestSystemCalcBase.setUp(self)

	def test_soc_sync(self):
		self._add_device('com.victronenergy.vecan.can0', values={
			'/Link/Soc': None,
			'/Link/ExtraBatteryCurrent': None})

		self._add_device('com.victronenergy.battery.ttyO4', product_name='battery', values={
				'/Dc/0/Voltage': 12.4,
				'/Dc/0/Current': 5.6,
				'/Dc/0/Power': 69.4,
				'/Soc': 53.2,
				'/Info/ChargeRequest': 0})


		self._update_values()
		self._check_external_values({
			'com.victronenergy.vecan.can0': {
				'/Link/Soc': 53.2,
			}})

	def test_extra_battery_current(self):
		self._add_device('com.victronenergy.vecan.can0', values={
			'/Link/Soc': None,
			'/Link/ExtraBatteryCurrent': None})
		self._add_device('com.victronenergy.solarcharger.ttyO4', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 9.7,
			'/FirmwareVersion': 0x0129},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.ttyO5', {
			'/State': 3,
			'/Link/NetworkMode': 0,
			'/Link/ChargeVoltage': None,
			'/Link/VoltageSense': None,
			'/Dc/0/Voltage': 12.4,
			'/Dc/0/Current': 10.3,
			'/FirmwareVersion': 0x0129},
			connection='VE.Direct')
		self._add_device('com.victronenergy.solarcharger.socketcan_can0_di0_uc30688', {
			'/Dc/0/Voltage': 12.6,
			'/Dc/0/Current': 5.0,
			'/FirmwareVersion': 0x102ff,
		}, connection='VE.Can')

		self._update_values()
		self._check_external_values({
			'com.victronenergy.vecan.can0': {
				'/Link/ExtraBatteryCurrent': 20.0, # Only VE.Direct chargers
			}})

		# Add a Multi
		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='Multi',
			values={
				'/Dc/0/Voltage': 12.4,
				'/Dc/0/Current': -8.0,
				'/Dc/0/Temperature': 24,
				'/ExtraBatteryCurrent': 0,
				'/Soc': 53.2,
				'/State': 3,
			})
		self._update_values()
		self._check_external_values({
			'com.victronenergy.vecan.can0': {
				'/Link/ExtraBatteryCurrent': 12.0, # Compensated for 8A drawn by Multi
			}})
