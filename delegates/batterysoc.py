from datetime import datetime, timedelta
import logging
from delegates.base import SystemCalcDelegate

PRECISION = 2

class BatterySoc(SystemCalcDelegate):
	def __init__(self, sc):
		super(BatterySoc, self).__init__()
		self.systemcalc = sc
		self.last_measurement = None
		self.last_soc = None
		self.one_percent_equivalent = 28400 / 100.0
		self.iamount = None
		self._isoc = None

	def get_output(self):
		return [
			('/Dc/Battery/Soc', {'gettext': '%.{}F %%'.format(PRECISION)})
			]

	@property
	def soc_bms(self):
		''' 
			soc as returned by the bms 
		'''
		if self.systemcalc.batteryservice is not None:
			return self._dbusmonitor.get_value(self.systemcalc.batteryservice, '/Soc')
		return None
	
	@property
	def soc(self):
		''' 
			Will return the interpolated soc if enabled and initialized, else the bms soc until
			interpolated becomes available.
		'''
		return self._isoc if self._isoc is not None else self.soc_bms

	def update_values(self, newvalues):
		current_soc = self.soc_bms
		try:
			now = datetime.now()
			current_power = self._dbusmonitor.get_value(self.systemcalc.batteryservice, '/Dc/0/Power')
			#before simply returning the soc to system, add the interpolated soc.
			#Pylontech seems to be flooring their reported soc.
			#The battery stays long at 99%, and when reaching 100% once, drops to 99% quite fast.
			#For interpolation that means: We need to reset the interpolation amount based on two
			#different observations:
			# - If the soc is reporting a "+1", the battery has actually moved to a state slightly above the prior value.
			#   -> interpolated amount has to be set to "0"
			# - If the soc is reporting a "-1", the battery has actually moved to a state slightly bellow the prior value.
			#   -> interpolated amount has to be set to "the amount equaling 1% of capacity."
			#
			if self.last_soc is None:
				newvalues['/Dc/Battery/Soc'] = current_soc
			else:
				#calculate based on power since last measurement, how much we assume the interpolated amount
				#has changed. We have to wait for iamount to be initialized by a soc change detected.
				if current_soc != self.last_soc:
					# initialize the interpolated_amount
					if (current_soc < self.last_soc):
						#dropping
						self.iamount = self.one_percent_equivalent
					
					if (current_soc > self.last_soc):
						#raising
						self.iamount = 0

				if self.iamount is not None:
					if self.last_measurement is not None:
						delta_seconds = (now - self.last_measurement).total_seconds()
						self.iamount += (current_power / 3600.0) * delta_seconds
						self.iamount = max(0, min(self.iamount, self.one_percent_equivalent))
					
					# the interpolated amount is Wh we belive to exist above soc. 
					# yet we are only interpolating "within the reported percent" to avoid derailing
					# due to long periods of noise. so clamp the iamount based offset to 0..1
					# worst case (iamount derailed) it'll be as good as the uninterpolated soc value.
					self._isoc = round(min(current_soc + max(min(self.iamount / self.one_percent_equivalent, 1.0), 0), 100.0), PRECISION)
					newvalues['/Dc/Battery/Soc'] = self._isoc
				else:
					#no interpolation possible right now, need to await soc sync.
					#newvalues['/Dc/Battery/iSoc'] = self.soc
					newvalues['/Dc/Battery/Soc'] = current_soc

			self.last_soc = current_soc
			self.last_measurement = now
		except Exception as ex:
			logging.getLogger().warning("Exception during soc interpolation: ", exc_info=ex)
			newvalues['/Dc/Battery/Soc'] = current_soc #oops, better return unmodified.

