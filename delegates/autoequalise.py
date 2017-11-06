from datetime import datetime, timedelta
import gobject
import time

# Victron packages
from ve_utils import exit_on_error

from delegates.base import SystemCalcDelegate


class AutoEqualise(SystemCalcDelegate):
	# Auto equalise states
	IDLE = 0
	CHARGETOABSORPTION = 1
	EQUALISING = 2

	# Vebus substates
	INITIALIZING = 0
	BULK = 1
	ABSORPTION = 2
	FLOAT = 3
	EQUALISE = 7
	FORCEEQCMD = 1

	def __init__(self):
		SystemCalcDelegate.__init__(self)

		gobject.idle_add(exit_on_error, lambda: not self._update_eq_state())
		gobject.timeout_add(5000, exit_on_error, self._update_eq_state)

	def set_sources(self, dbusmonitor, settings, dbusservice):
		SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
		self._dbusservice.add_path('/AutoEqualise/State', value=AutoEqualise.IDLE)
		self._dbusservice.add_path('/AutoEqualise/ManualEqualisation', value=0, writeable=True)
		self._dbusservice.add_path('/AutoEqualise/NextEqualisation', value=0)

	def _update_eq_state(self):
		vebusservice = self._dbusservice['/VebusService']
		autoeqenabled = self._settings['autoeqenabled'] == 1
		manualeq = self._dbusservice['/AutoEqualise/ManualEqualisation'] == 1
		autoeqstate = self._dbusservice['/AutoEqualise/State']

		if not vebusservice or not (autoeqenabled or manualeq or autoeqstate != AutoEqualise.IDLE):
			return True

		now = datetime.now()
		firsteqdate = datetime.strptime(self._settings['autoeqfirsteqdate'], '%Y-%m-%d')
		starttime = datetime.strptime(self._settings['autoeqstarttime'], '%H:%M').time()
		interval = self._settings['autoeqinterval']
		gap = (now.date() - firsteqdate.date()).days % interval
		lasteq = datetime.fromtimestamp(self._settings['autoeqlastcompleted'])
		chargerstate = self._dbusmonitor.get_value(vebusservice, '/VebusSubstate')
		autoeqstate = self._dbusservice['/AutoEqualise/State']
		manualeq = self._dbusservice['/AutoEqualise/ManualEqualisation'] == 1

		# If state is not idle means that the process already started so set start to true
		# this is necessary in case day changes during equalise process
		start = (autoeqenabled and autoeqstate != AutoEqualise.IDLE) or manualeq

		# Determine if auto equalise should start and the next equalisation date
		if firsteqdate.date() > now.date():
			# First EQ is set to a date in the future, wait till then
			nextdate = firsteqdate.replace(hour=starttime.hour, minute=starttime.minute, second=0)
			self._dbusservice['/AutoEqualise/NextEqualisation'] = time.mktime(nextdate.timetuple())
		elif gap == 0 and lasteq.date() != now.date() or start:
			startdatetime = now.replace(hour=starttime.hour, minute=starttime.minute)
			self._dbusservice['/AutoEqualise/NextEqualisation'] = time.mktime(startdatetime.timetuple())
			start = now >= startdatetime or start
		else:
			nextdate = now + timedelta(days=interval - gap)
			nextdate = nextdate.replace(hour=starttime.hour, minute=starttime.minute, second=0)
			self._dbusservice['/AutoEqualise/NextEqualisation'] = time.mktime(nextdate.timetuple())

		# Set auto equalise and charger states
		if start and chargerstate >= 0:
			# Consider auto equalisation timed out when absorption state not reached after 10 hours
			timedout = (now - datetime.fromtimestamp(self._settings['autoeqlaststarted'])).seconds / 3600 >= 10
			if autoeqstate == AutoEqualise.CHARGETOABSORPTION and timedout:
				self._dbusservice['/AutoEqualise/State'] = AutoEqualise.IDLE
				self._dbusservice['/AutoEqualise/ManualEqualisation'] = 0
			elif chargerstate != AutoEqualise.EQUALISE and autoeqstate == AutoEqualise.EQUALISING:
				# Equalisation finished or interrupted when the charger state switches to any other state
				if not manualeq:
					self._settings['autoeqlastcompleted'] = self._settings['autoeqlaststarted']
				self._dbusservice['/AutoEqualise/State'] = AutoEqualise.IDLE
				self._dbusservice['/AutoEqualise/ManualEqualisation'] = 0

			elif chargerstate in [AutoEqualise.INITIALIZING, AutoEqualise.BULK]:
				# Don't start equalising when state is bulk, wait till absorption reached
				if autoeqstate == AutoEqualise.IDLE:
					self._settings['autoeqlaststarted'] = time.time()
				self._dbusservice['/AutoEqualise/State'] = AutoEqualise.CHARGETOABSORPTION
			elif chargerstate != AutoEqualise.EQUALISE:
				# Charger not equalising yet, send the start command and store the start time.
				if autoeqstate == AutoEqualise.IDLE and not manualeq:
					self._settings['autoeqlaststarted'] = time.time()
				self._dbusmonitor.set_value(vebusservice, '/VebusSetChargeState', AutoEqualise.FORCEEQCMD)
			elif chargerstate == AutoEqualise.EQUALISE and self._dbusservice['/AutoEqualise/State'] != AutoEqualise.EQUALISING:
				self._dbusservice['/AutoEqualise/State'] = AutoEqualise.EQUALISING
		elif chargerstate >= 0:
			if autoeqstate != AutoEqualise.IDLE:
				self._dbusservice['/AutoEqualise/State'] = AutoEqualise.IDLE

		return True

	def get_settings(self):
		return [
			('autoeqenabled', '/Settings/AutoEqualise/Enabled', 0, 0, 20),
			('autoeqfirsteqdate', '/Settings/AutoEqualise/StartDate', '2016-01-01', 0, 0),
			('autoeqstarttime', '/Settings/AutoEqualise/StartTime', '14:00', 0, 0),
			('autoeqinterval', '/Settings/AutoEqualise/Interval', 180, 1, 365),
			('autoeqlaststarted', '/Settings/AutoEqualise/LastStarted', 0, 0, 0),
			('autoeqlastcompleted', '/Settings/AutoEqualise/LastCompleted', 0, 0, 0),
			('autoequalisecurrent', '/Settings/AutoEqualise/MaxChargeCurrent', 50, 0, 10000)]

	def get_input(self):
		return [
			('com.victronenergy.vebus', ['/VebusSubstate', '/VebusSetChargeState']),
			('com.victronenergy.settings', ['/Settings/System/TimeZone'])]
