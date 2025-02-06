# Base class for classes that wants to control charging via hub4control
import logging
logger = logging.getLogger(__name__)

class ChargeControl(object):
	controller = None
	control_priority = 0

	@property
	def can_acquire_control(self):
		return ChargeControl.controller is None or \
			ChargeControl.controller is self.__class__ or \
			self.control_priority < ChargeControl.controller.control_priority

	def acquire_control(self):
		if self.can_acquire_control:
			ChargeControl.controller = self.__class__
			return True
		return False

	def release_control(self):
		if ChargeControl.controller is self.__class__:
			ChargeControl.controller = None

	def has_control(self):
		return ChargeControl.controller is self.__class__
