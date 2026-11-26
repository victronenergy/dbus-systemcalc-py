import unittest
import context
import patches
from base import TestSystemCalcBase
from delegates.servicemapper import ServiceMapper


class ServiceMapperReplacementTest(TestSystemCalcBase):
	def test_removing_old_service_preserves_replacement(self):
		old = 'com.victronenergy.temperature.old'
		new = 'com.victronenergy.temperature.new'
		for service in (old, new):
			self._add_device(service, {'/DeviceInstance': 7, '/Temperature': 20})
		path = ServiceMapper._get_service_mapping_path(new, 7)
		self.assertEqual(self._service[path], new)
		self._remove_device(old)
		self.assertEqual(self._service[path], new)
		self._remove_device(new)
		self.assertNotIn(path, self._service)
