class TrackInstance(type):
	def __init__(klass, name, bases, attrs):
		if not hasattr(klass, '_instance'):
			klass._instance = None
		else:
			if klass._instance is not None:
				raise RuntimeError("Multiple instances of {}".format(klass.__name__))
			klass._instance = klass

	@property
	def instance(klass):
		return klass._instance

class SystemCalcDelegate(object):
	__metaclass__ = TrackInstance
	def __new__(klass, *args, **kwargs):
		klass._instance = super(SystemCalcDelegate, klass).__new__(klass)
		return klass._instance

	def __init__(self):
		self._dbusmonitor = None
		self._settings = None
		self._dbusservice = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		self._dbusmonitor = dbusmonitor
		self._settings = settings
		self._dbusservice = dbusservice

	def get_input(self):
		"""In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to populate self._dbusmonitor. Paths should be ordered by service name.
		Example:
		def get_input(self):
			return [
				('com.victronenergy.battery', ['/ProductId']),
				('com.victronenergy.solarcharger', ['/ProductId'])]
		"""
		return []

	def get_output(self):
		"""In derived classes this function should return the list or D-Bus paths used as input. This will be
		used to create the D-Bus items in the com.victronenergy.system service. You can include a gettext
		field which will be used to format the result of the GetText reply.
		Example:
		def get_output(self):
			return [('/Hub', {'gettext': '%s'}), ('/Dc/Battery/Current', {'gettext': '%s A'})]
		"""
		return []

	def get_settings(self):
		"""In derived classes this function should return all settings (from com.victronenergy.settings)
		that are used in this class. The return value will be used to populate self._settings.
		Note that if you add a setting here, it will be created (using AddSettings of the D-Bus), if you
		do not want that, add your setting to the list returned by get_input.
		List item format: (<alias>, <path>, <default value>, <min value>, <max value>)
		def get_settings(self):
			return [('writevebussoc', '/Settings/SystemSetup/WriteVebusSoc', 0, 0, 1)]
		"""
		return []

	def settings_changed(self, setting, oldvalue, newvalue):
		""" A delegate can monitor a particular setting by implementing
		    settings_changed. """
		pass

	def battery_service_changed(self, oldservice, newservice):
		""" If the battery monitor changes, delegates can hook into
		    that event by implementing battery_monitor_changed. """
		pass

	def update_values(self, newvalues):
		pass

	def device_added(self, service, instance, do_service_change=True):
		pass

	def device_removed(self, service, instance):
		pass
