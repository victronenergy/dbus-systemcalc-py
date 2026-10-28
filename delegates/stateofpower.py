from gi.repository import GLib

# Victron packages
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate
from delegates.batteryservice import BatteryService
from delegates.chargeheadroom import ChargeHeadroom

class StateOfPower(SystemCalcDelegate):
	""" Estimates how much charge current the battery can take before the
	    highest cell reaches its limit. Proof of concept. """

	def __init__(self):
		super(StateOfPower, self).__init__()
		self._estimator = None
		self._service = None

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(StateOfPower, self).set_sources(dbusmonitor, settings, dbusservice)
		# The estimator expects samples at a steady rate, so don't rely on
		# update_values, which only runs when something changed.
		GLib.timeout_add(1000, exit_on_error, self._on_timer)

		# Not in get_output, because systemcalc resets those every update
		self._dbusservice.add_path('/Dc/Battery/Sop/R', value=None,
			gettextcallback=lambda p, v: '' if v is None else f'{v:.3f} mOhm')
		self._dbusservice.add_path('/Dc/Battery/Sop/C', value=None,
			gettextcallback=lambda p, v: '' if v is None else f'{v:.1f} mV')
		self._dbusservice.add_path('/Dc/Battery/Sop/ChargeCurrent', value=None,
			gettextcallback=lambda p, v: '' if v is None else f'{v:.1f} A')
		self._dbusservice.add_path('/Dc/Battery/Sop/CurrentStep', value=None,
			gettextcallback=lambda p, v: '' if v is None else f'{v:.1f} A')

	@property
	def capacity(self):
		""" Battery capacity in Ah, or None if unknown. """
		return self._dbusservice['/Dc/Battery/Capacity']

	def _publish(self, r, c, i, s):
		self._dbusservice['/Dc/Battery/Sop/R'] = r
		self._dbusservice['/Dc/Battery/Sop/C'] = c
		self._dbusservice['/Dc/Battery/Sop/ChargeCurrent'] = i
		self._dbusservice['/Dc/Battery/Sop/CurrentStep'] = s

	def _on_timer(self):
		battery = BatteryService.instance.bms or BatteryService.instance.batteryservice
		vmax = battery.maxcellvoltage if battery is not None else None
		current = self._dbusmonitor.get_value(battery.service, '/Dc/0/Current') \
			if battery is not None else None

		if vmax is None or current is None:
			self._estimator = self._service = None
			self._publish(None, None, None, None)
			return True

		if self._estimator is None or battery.service != self._service:
			self._estimator = ChargeHeadroom(capacity=lambda: self.capacity)
			self._service = battery.service

		i_avail = self._estimator.update(vmax, current)
		self._publish(
			round(self._estimator.resistance * 1e3, 3),
			round(self._estimator.drift * 1e3, 1),
			round(i_avail, 1),
			round(self._estimator.min_current_step, 1))
		return True
