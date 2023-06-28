from gi.repository import GLib
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc

INTERVAL = 10
HUB4_SERVICE = 'com.victronenergy.hub4'

MODES = {
	0: 'Disabled',
	1: 'Idle',
	2: 'Import',
	3: 'Export'
}

class DynamicEss(SystemCalcDelegate):

	def __init__(self):
		super(DynamicEss, self).__init__()
		self.pvpower = 0
		self._timer = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(DynamicEss, self).set_sources(dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/DynamicEss/Active', value=False)
		self._dbusservice.add_path('/DynamicEss/Timeout', None, writeable=True,
			onchangecallback=self._on_timeout_changed)
		self._dbusservice.add_path('/DynamicEss/Mode', 0, writeable=True,
			onchangecallback=self._on_mode_changed,
			gettextcallback=lambda p, v: MODES.get(v, 'Unknown'))

	def get_settings(self):
		return [
			('dynamic_ess_minsoc', '/Settings/DynamicEss/MinSoc', 20.0, 0.0, 100.0)
		]

	def get_input(self):
		return [
			(HUB4_SERVICE, ['/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower', '/Overrides/Setpoint',
				'/Overrides/FeedInExcess'])
		]

	@property
	def active(self):
		return bool(self._dbusservice['/DynamicEss/Active'])

	@active.setter
	def active(self, v):
		self._dbusservice['/DynamicEss/Active'] = int(bool(v))

	@property
	def mode(self):
		return self._dbusservice['/DynamicEss/Mode']
	
	@mode.setter
	def mode(self, v):
		self._dbusservice['/DynamicEss/Mode'] = v

	@property
	def timeout(self):
		return self._dbusservice['/DynamicEss/Timeout']
	
	@timeout.setter
	def timeout(self, v):
		self._dbusservice['/DynamicEss/Timeout'] = v
	
	@property
	def minsoc(self):
		return self._settings['dynamic_ess_minsoc']

	@property
	def soc(self):
		return BatterySoc.instance.soc

	def _on_mode_changed(self, path, value):
		if value == 0:
			GLib.idle_add(self._on_timeout)
		else:
			self.restart(self.timeout)
		return 0 <= value <= 3

	def _on_timeout_changed(self, path, value):
		self.restart(value)
		return True
	
	def restart(self, timeout):
		if timeout is not None and timeout > 0:
			# We're about to set a new timer, remove an old one if there is
			# one.
			if self._timer is not None:
				GLib.source_remove(self._timer)

			self.active = True
			GLib.idle_add(lambda: self._on_timer(0) and False)
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

		elif (timeout is None or timeout == 0) and self._timer is not None:
			GLib.idle_add(self._on_timeout);

	def _on_timer(self, delta=INTERVAL):
		mode = self.mode
		if mode == 3: # Export
			if self.soc > self.minsoc:
				self.do_export()
			else:
				self.do_idle()
		elif mode == 2: # Import
			self.do_import()
		elif mode == 1: # Idle
			# Prevent battery discharge, no feed-in
			self.do_idle()

		remaining = self.timeout or 0
		if remaining > delta:
			self.timeout = remaining - delta
			return True

		self._on_timeout()
		return False

	def _on_timeout(self):
		self.timeout = None
		self.mode = 0
		self.active = False

		# Reset hub4 values
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 1)

		if self._timer is not None:
			GLib.source_remove(self._timer)
		self._timer = None

	def do_idle(self):
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)

		# Allow some of the PV to be used for loads. No less than 1W,
		# so peak-shaving continues to work.
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower',
			max(1.0, round(0.8*self.pvpower)))
	
	def do_import(self):
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', None)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 1)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 0)

	def do_export(self):
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/Setpoint', -32000) # Export hard as we can
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/ForceCharge', 0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', -1.0)
		self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/FeedInExcess', 1)

	def update_values(self, newvalues):
		self.pvpower = newvalues.get('/Dc/Pv/Power') or 0
