import context
import patches
from base import TestSystemCalcBase

class ReplacementBmsTest(TestSystemCalcBase):
	def test_explicitly_selected_replacement_is_not_deleted(self):
		old = 'com.victronenergy.battery.old'
		new = 'com.victronenergy.battery.new'
		self._set_setting('/Settings/SystemSetup/BmsInstance', 7)
		for service in (old, new):
			self._add_device(service, {'/DeviceInstance': 7, '/Soc': 50,
				'/Dc/0/Voltage': 52, '/Info/MaxChargeVoltage': 55})
		self._update_values()
		self._check_values({'/ActiveBmsService': new, '/ActiveBmsInstance': 7})
		self._remove_device(old)
		self._update_values()
		self._check_values({'/ActiveBmsService': new, '/ActiveBmsInstance': 7})
		self._remove_device(new)
		self._update_values()
		self._check_values({'/ActiveBmsService': None})
