# Victron packages
from sc_utils import service_instance_name
from delegates.base import SystemCalcDelegate

class ServiceMapper(SystemCalcDelegate):
	def device_added(self, service, instance, do_service_change=True):
		path = ServiceMapper._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			self._dbusservice[path] = service
		else:
			self._dbusservice.add_path(path, service)

	def device_removed(self, service, instance):
		path = ServiceMapper._get_service_mapping_path(service, instance)
		if path in self._dbusservice:
			del self._dbusservice[path]

	@staticmethod
	def _get_service_mapping_path(service, instance):
		sn = service_instance_name(service, instance).replace('.', '_').replace('/', '_')
		return '/ServiceMapping/%s' % sn
