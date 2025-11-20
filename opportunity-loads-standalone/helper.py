import logging
import json
from enum import Enum, IntFlag

class Modes(int, Enum):
	Off = 0
	On = 1

class ConsumerType(int, Enum):
	Primary = 0
	Secondary = 1

class SystemTypeFlag(IntFlag):
	None_ = 0
	SinglePhase = 1
	DualPhase = 2
	ThreePhase = 4
	GridConnected = 8
	OffGrid = 16
	Saldating = 32
	Individual = 64
	FeedinAllowed = 128
	ZeroFeedin = 256

	def to_str(value: int) -> str:
		members = [flag.name for flag in SystemTypeFlag if flag & value]
		return "{}:".format(value) + ("|".join(members) if members else str(value))

class ClaimType(int, Enum):
	Total = 0
	AC = 1
	DC = 2
	ACDCAC = 3

class NoDebugInfoWarningPropagationLogger(logging.Logger):
    def callHandlers(self, record):
        # Handle with this logger's handlers
        c = self
        found = 0
        while c:
            for hdlr in c.handlers:
                if record.levelno >= hdlr.level:
                    hdlr.handle(record)
                    found = 1
            # Prevent DEBUG logs from propagating
            if record.levelno <= logging.WARNING:
                break
            if not c.propagate:
                break
            c = c.parent
        if not found:
            logging.lastResort.handle(record)

class LevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level
    def filter(self, record):
        return record.levelno >= self.level
    
class Configurable():
	def __init__(self, system_path:str, settings_path:str, settings_key:str, default_value, min_value, max_value, configurables, decode_payload=False) :
		self._system_path = system_path
		self._settings_path = settings_path
		self._settings_key = settings_key
		self._default_value = default_value
		self._current_value = default_value #init to default
		self._min_value = min_value
		self._max_value = max_value
		self._decode_payload = decode_payload
		configurables.append(self)
	
	@property
	def system_path(self) -> str:
		return self._system_path
	
	@property
	def settings_path(self) -> str:
		return self._settings_path
	
	@property
	def settings_key(self) -> str:
		return self._settings_key
	
	@property
	def default_value(self):
		return self._default_value
	
	@property
	def min_value(self):
		return self._min_value

	@property
	def max_value(self):
		return self._max_value

	@property
	def current_value(self):
		return self._current_value
	
	@current_value.setter
	def current_value(self, v):
		if self._decode_payload:
			self._current_value = json.loads(v)
		else:
			self._current_value=v
	
	def force_write(self, settings):
		"""
			forces a rewrite of current value to settings.
		"""
		if self._decode_payload:
			settings[self.settings_key] = json.dumps(self.current_value)
		else:
			settings[self.settings_key] = self.current_value