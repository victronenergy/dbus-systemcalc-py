from delegates.base import SystemCalcDelegate
from delegates.multi import Multi
from sc_utils import safeadd

class Service(object):
	def __init__(self, service, instance):
		self.service = service
		self.instance = instance

class InverterCharger(SystemCalcDelegate):
	def __init__(self):
		super(InverterCharger, self).__init__()
		self.devices = {}

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)

	def get_input(self):
		return [
		('com.victronenergy.inverter', [
				'/Dc/0/Voltage',
				'/Dc/0/Current',
				'/Dc/0/Power',
				'/Ac/Out/L1/P',
				'/Ac/Out/L2/P',
				'/Ac/Out/L3/P',
				'/Ac/Out/L1/V',
				'/Ac/Out/L2/V',
				'/Ac/Out/L3/V',
				'/Ac/Out/L1/I',
				'/Ac/Out/L2/I',
				'/Ac/Out/L3/I']),
		('com.victronenergy.multi', [
				'/Dc/0/Voltage',
				'/Dc/0/Current',
				'/Dc/0/Power',
				'/Ac/Out/L1/P',
				'/Ac/Out/L2/P',
				'/Ac/Out/L3/P',
				'/Ac/Out/L1/V',
				'/Ac/Out/L2/V',
				'/Ac/Out/L3/V',
				'/Ac/Out/L1/I',
				'/Ac/Out/L2/I',
				'/Ac/Out/L3/I'])
		]

	def get_output(self):
		return [
			('/Dc/InverterCharger/Current', {'gettext': '%.1F A'}),
			('/Dc/InverterCharger/Power', {'gettext': '%.0F W'})]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.multi.'):
			self.devices[service] = Service(service, instance)
		elif service.startswith('com.victronenergy.inverter.'):
			self.devices[service] = Service(service, instance)

	def device_removed(self, service, instance):
		if service in self.devices:
			del self.devices[service]

	def update_values(self, newvalues):
		vebus_service = vebus_instance = None
		try:
			vebus_service =  Multi.instance.vebus_service.service
			vebus_instance =  Multi.instance.vebus_service.instance
		except AttributeError:
			pass

		if vebus_service is not None:
			newvalues['/Dc/InverterCharger/Current'] = \
				self._dbusmonitor.get_value(vebus_service, '/Dc/0/Current')
			newvalues['/Dc/InverterCharger/Power'] = \
				self._dbusmonitor.get_value(vebus_service, '/Dc/0/Power')
		else:
			power = None
			current = None
			for device in self.devices:
				p = self._dbusmonitor.get_value(device, '/Dc/0/Power')
				c = self._dbusmonitor.get_value(device, '/Dc/0/Current')

				if p is None:
					# No power value, calculate from current if we have it
					v = self._dbusmonitor.get_value(device, '/Dc/0/Voltage')
					if c is None:
						# No DC current value, try AC power value
						for phase in range(1, 4):
							acp = self._dbusmonitor.get_value(device,
								f"/Ac/Out/L{phase}/P")
							if acp is None:
								# No AC power value, work backwards from AC
								# amps
								acc = self._dbusmonitor.get_value(device,
									f"/Ac/Out/L{phase}/I")
								acv = self._dbusmonitor.get_value(device,
									f"/Ac/Out/L{phase}/V")
								try:
									p = acv * acc
									c = p / v
								except (TypeError, ZeroDivisionError):
									pass
								else:
									power = safeadd(power, -p)
									current = safeadd(current, -c)
							else:
								power = safeadd(power, -acp)
								try:
									current = safeadd(current, -acp / v)
								except (TypeError, ZeroDivisionError):
									pass
					else:
						power = safeadd(power, v * c)
				else:
					power = safeadd(power, p)
					current = safeadd(current, c)

			newvalues['/Dc/InverterCharger/Current'] = current
			newvalues['/Dc/InverterCharger/Power'] = power
