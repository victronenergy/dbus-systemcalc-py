from delegates.base import SystemCalcDelegate
from delegates.dvcc import Dvcc

class SocService(object):
	def __init__(self, monitor, service, instance):
		self.monitor = monitor
		self.service = service
		self.instance = instance

	@property
	def isBmv(self):
		pid = self.monitor.get_value(self.service, '/ProductId')
		return (0x200 <= pid < 0x211) or (0xA380 <= pid <= 0xA38F)

class BatterySoc(SystemCalcDelegate):
	def __init__(self, sc):
		super(BatterySoc, self).__init__()
		self.systemcalc = sc
		self.socs = {}
		self.bmv = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		dbusservice.add_path('/Control/UseBmvForSoc', value=None, writeable=True,
			onchangecallback=self.update_setting)

	def update_setting(self, path, value):
		if Dvcc.instance.has_dvcc and self.bmv:
			self.bmvsoc = value
			return True
		return False

	def get_settings(self):
		return [
			('bmvsoc', '/Settings/SystemSetup/UseBmvForSoc', 0, 0, 1),
		]

	def get_output(self):
		return [('/Dc/Battery/Soc', {'gettext': '%.0F %%'})]

	def device_added(self, service, instance, *args):
		# Look for battery monitors
		if service.startswith('com.victronenergy.battery.'):
			self.socs[service] = SocService(self._dbusmonitor, service, instance)
			self.update_socs()

	def device_removed(self, service, instance):
		if service in self.socs:
			del self.socs[service]
			self.update_socs()

	def update_socs(self):
		# If there is more than one battery monitor, and one of them is
		# a BMV, then we allow the BMV with the lowest device instance
		# to override the SOC of the other battery monitor (typically
		# a BMS).
		bmvs = [s for s in self.socs.itervalues() if s.isBmv]
		if bmvs:
			bmvs.sort(key=lambda x: x.instance)
			self.bmv = bmvs[0]
			self._dbusservice['/Control/UseBmvForSoc'] = self.bmvsoc
			return

		self.bmv = None
		self._dbusservice['/Control/UseBmvForSoc'] = None

	@property
	def bmvsoc(self):
		return self._settings['bmvsoc']

	@bmvsoc.setter
	def bmvsoc(self, value):
		self._settings['bmvsoc'] = int(bool(value))

	def _default_soc(self):
		if self.systemcalc.batteryservice is not None:
			return self._dbusmonitor.get_value(self.systemcalc.batteryservice, '/Soc')
		return None

	def update_values(self, newvalues):
		soc = None
		if Dvcc.instance.has_dvcc and self.bmv and self.systemcalc.batteryservice != self.bmv.service:
			if self.bmvsoc:
				soc = self._dbusmonitor.get_value(self.bmv.service, '/Soc')
				self._dbusservice['/Control/UseBmvForSoc'] = 1
			else:
				soc = self._default_soc()
				self._dbusservice['/Control/UseBmvForSoc'] = 0
		else:
			soc = self._default_soc()
			self._dbusservice['/Control/UseBmvForSoc'] = None

		newvalues['/Dc/Battery/Soc'] = soc
