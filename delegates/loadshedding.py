from datetime import datetime, timedelta
from gi.repository import GLib
from delegates.base import SystemCalcDelegate
from delegates.schedule import ScheduledWindow
from delegates.batterysoc import BatterySoc
from delegates.multi import Multi
from delegates.chargecontrol import ChargeControl

NUM_SCHEDULES = 4
INTERVAL = 5
HUB4_SERVICE = 'com.victronenergy.hub4'

ACTIVE = {
       0: 'Off',
       1: 'Load-shedding',
	   2: 'Preparing'
}

ERRORS = {
	0: 'No error',
}


class SwitchableDevice(object):
	def __init__(self, monitor, service):
		self.monitor = monitor
		self.service = service

	def connect(self):
		raise NotImplementedError("connect")

	def disconnect(self):
		raise NotImplementedError("disconnect")

	def prepare(self):
		raise NotImplementedError("prepare")

class MultiRs(SwitchableDevice):
	@property
	def ac_in_type(self):
		return self.monitor.get_value(self.service, '/Ac/In/1/Type')

	def connect(self):
		if self.ac_in_type == 1: # Grid
			# For now we use /Mode. In future this will hopefully use ignore AC.
			self.monitor.set_value_async(self.service, '/Mode', 3)

	def disconnect(self):
		if self.ac_in_type == 1: # Grid
			self.monitor.set_value_async(self.service, '/Mode', 2)

	def prepare(self):
		# Not supported yet. Could set /Settings/Ess/Mode to 2 (Keep charged)
		# but that is a flash write. So for now, not supported.
		pass

class LoadSheddingWindow(ScheduledWindow):
	def __init__(self, start, duration):
		super(LoadSheddingWindow, self).__init__(start, duration)

class LoadShedding(SystemCalcDelegate, ChargeControl):
	control_priority = 10
	_get_time = datetime.now

	def __init__(self):
		super(LoadShedding, self).__init__()
		self._timer = None
		self.devices = {}

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(LoadShedding, self).set_sources(dbusmonitor, settings, dbusservice)
		# Future path for capabilities
		self._dbusservice.add_path('/LoadShedding/Capabilities', value=0)
		self._dbusservice.add_path('/LoadShedding/Available', value=1)
		self._dbusservice.add_path('/LoadShedding/Active', value=0,
			gettextcallback=lambda p, v: ACTIVE.get(v, 'Unknown'))
		self._dbusservice.add_path('/LoadShedding/ErrorCode', value=0,
			gettextcallback=lambda p, v: ERRORS.get(v, 'Unknown'))
		self._dbusservice.add_path('/LoadShedding/NextDisconnect', value=None,
			gettextcallback=lambda p, v: datetime.fromtimestamp(v).isoformat())

		if self.mode > 0:
			self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def get_settings(self):
		# Settings for LoadShedding
		path = '/Settings/LoadSheddingApi'

		settings = [
			# Mode, either on or off for now
			("loadshedding_mode", path + "/Mode", 0, 0, 1),
			# How long before load shedding starts to recharge the battery,
			# default 1 hour
			("loadshedding_preparetime", path + "/PreparationTime", 3600, 0, 0),
			# How long before the slot to disconnect, default 5 minutes
			("loadshedding_disconnectmargin", path + "/DisconnectMargin", 300, 0, 0),
			# How long before the slot ends to attempt reconnection, default 30 minutes
			("loadshedding_reconnectmargin", path + "/ReconnectMargin", 1800, 0, 0),
			# Minimum SOC ahead of an outage
			("loadshedding_minsoc", path + "/MinSoc", 0, 0, 100),
			# A place to store the token used for the external API. mqtt-rpc
			# needs this for the proxy-json command.
			("loadshedding_token", path + "/Token", "", 0, 0),
		]

		for i in range(NUM_SCHEDULES):
			settings.append(("loadshedding_start_{}".format(i),
				path + "/Schedule/{}/Start".format(i), 0, 0, 0))
			settings.append(("loadshedding_duration_{}".format(i),
				path + "/Schedule/{}/Duration".format(i), 0, 0, 0))

		return settings

	def get_input(self):
		return [
			(HUB4_SERVICE, [
				'/Overrides/ForceCharge',
				'/Overrides/MaxDischargePower'
			]),
			('com.victronenergy.multi', [
				'/Ac/In/1/Type',
				'/Mode',
			]),
		]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'loadshedding_mode':
			if oldvalue == 0 and newvalue > 0:
				self._timer = GLib.timeout_add(INTERVAL * 1000, self._on_timer)

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.multi.'):
			self.devices[service] = MultiRs(self._dbusmonitor, service)

	def device_removed(self, service, instance):
		try:
			del self.devices[service]
		except KeyError:
			pass

	def windows(self, now):
		starttimes = (self._settings['loadshedding_start_{}'.format(i)] for i in range(NUM_SCHEDULES))
		durations = (self._settings['loadshedding_duration_{}'.format(i)] for i in range(NUM_SCHEDULES))

		for start, duration in zip(starttimes, durations):
			# Check that start time is set to something and that the end of the
			# time slot is not in the past already.
			if start > 0 and datetime.fromtimestamp(start + duration) > now:
				duration = max(0, duration - self.reconnectmargin)
				yield LoadSheddingWindow(
					datetime.fromtimestamp(start - self.disconnectmargin), duration)

	@property
	def mode(self):
		return self._settings['loadshedding_mode']

	@property
	def minsoc(self):
		return self._settings['loadshedding_minsoc']

	@property
	def preparetime(self):
		return self._settings['loadshedding_preparetime']

	@property
	def disconnectmargin(self):
		return self._settings['loadshedding_disconnectmargin']

	@property
	def reconnectmargin(self):
		return self._settings['loadshedding_reconnectmargin']

	@property
	def active(self):
		return self._dbusservice['/LoadShedding/Active']

	@active.setter
	def active(self, v):
		self._dbusservice['/LoadShedding/Active'] = v

	@property
	def errorcode(self):
		return self._dbusservice['/LoadShedding/ErrorCode']

	@errorcode.setter
	def errorcode(self, v):
		self._dbusservice['/LoadShedding/ErrorCode'] = v

	@property
	def soc(self):
		return BatterySoc.instance.soc

	@property
	def pvpower(self):
		return self._dbusservice['/Dc/Pv/Power'] or 0

	@property
	def forcecharge(self):
		return self._dbusmonitor.get_value(HUB4_SERVICE, '/Overrides/ForceCharge')

	@forcecharge.setter
	def forcecharge(self, v):
		return self._dbusmonitor.set_value_async(HUB4_SERVICE,
			'/Overrides/ForceCharge', 1 if v else 0)

	@property
	def maxdischargepower(self):
		return self._dbusmonitor.get_value(HUB4_SERVICE, '/Overrides/MaxDischargePower')

	@maxdischargepower.setter
	def maxdischargepower(self, v):
		return self._dbusmonitor.set_value_async(HUB4_SERVICE, '/Overrides/MaxDischargePower', v)

	def connect(self):
		# Connect the main VE.Bus instance
		multi = Multi.instance.multi
		if multi is not None:
			for inp, t in multi.input_types:
				if t in (1, 3): # GRID, SHORE
					multi.set_ignore_ac(inp, False)

		# Other devices, such as MultiRS
		for dev in self.devices.values():
			dev.connect()

	def disconnect(self):
		# Disconnect the main VE.Bus instance
		multi = Multi.instance.multi
		if multi is not None:
			for inp, t in multi.input_types:
				if t in (1, 3): # GRID, SHORE
					multi.set_ignore_ac(inp, True)

		# Other devices, such as MultiRS
		for dev in self.devices.values():
			dev.disconnect()

	def prepare(self):
		# Tell Multi  to charge. This only works on ESS systems
		multi = Multi.instance.multi
		if multi is not None and multi.has_ess_assistant and self.soc is not None:
			if self.soc + 1.0 < self.minsoc:
				self.forcecharge = 1
				self.maxdischargepower = -1
			else:
				if self.soc < self.minsoc:
					self.forcecharge = self.forcecharge
				else:
					self.forcecharge = 0

				if self.forcecharge or self.soc > self.minsoc + 4.0:
					self.maxdischargepower = -1
				else:
					# Try to hold the SOC by limiting discharge to PV
					# Scale factor so we end up slightly over.
					scale = min(1.0, 0.8 + max(0, self.soc - self.minsoc) * 0.05)
					self.maxdischargepower = max(1, round(self.pvpower*scale))

		# Other devices, such as MultiRS
		for dev in self.devices.values():
			dev.prepare()

	def _on_timer(self):
		# If LS was disabled, deactivate and kill timer.
		if self.mode == 0:
			self.deactivate(0) # No error
			return False

		# self.mode == 1
		now = self._get_time()
		start = None
		stop = None

		windows = list(self.windows(now))
		nextshed = None
		for w in windows:
			if now < w.start and (nextshed is None or w.start < nextshed):
				nextshed = w.start

		self._dbusservice['/LoadShedding/NextDisconnect'] = \
			None if nextshed is None else int(datetime.timestamp(nextshed))

		for w in windows:
			if now in w and self.acquire_control():
				self.active = 1 # Auto
				self.disconnect()
				break # skip else below
		else:
			# No matching windows, check if we have to prepare
			self.connect()
			for w in windows:
				if now < w.start and now + timedelta(seconds=self.preparetime) >= w.start and self.acquire_control():
					self.active = 2
					self.prepare()
					break # Skip else
			else:
				if self.active:
					self.deactivate(0)

		return True

	def deactivate(self, reason):
		self.release_control()
		self.active = 0 # Off
		self.errorcode = reason
