class MockTimer(object):
	def __init__(self, start, timeout, callback, *args, **kwargs):
		self._timeout = timeout
		self._next = start + timeout
		self._callback = callback
		self._args = args
		self._kwargs = kwargs

	def run(self):
		self._next += self._timeout
		return self._callback(*self._args, **self._kwargs)

	@property
	def next(self):
		return self._next


class MockTimerManager(object):
	def __init__(self):
		self._timers = []
		self._time = 0

	def add_timer(self, timeout, callback, *args, **kwargs):
		self._timers.append(MockTimer(self._time, timeout, callback, *args, **kwargs))

	def add_idle(self, callback, *args, **kwargs):
		self.add_timer(self._time, callback, *args, **kwargs)

	def add_terminator(self, timeout):
		self.add_timer(timeout, self._terminate)

	def _terminate(self):
		raise StopIteration()

	@property
	def time(self):
		return self._time

	def start(self):
		try:
			while True:
				next_timer = None
				for t in self._timers:
					if next_timer == None or t.next < next_timer.next:
						next_timer = t
				if next_timer == None:
					return
				self._time = next_timer.next
				if not next_timer.run():
					self._timers.remove(next_timer)
		except StopIteration:
			self._timers.remove(next_timer)
			pass

	def reset(self):
		self._timers = []
		self._time = 0


timer_manager = MockTimerManager()


def idle_add(callback, *args, **kwargs):
	timer_manager.add_idle(callback, *args, **kwargs)


def timeout_add(timeout, callback, *args, **kwargs):
	timer_manager.add_timer(timeout, callback, *args, **kwargs)


def timeout_add_seconds(timeout, callback, *args, **kwargs):
	timeout_add(timeout * 1000, callback, *args, **kwargs)


def test_function(m, name):
	print(m.time, name)
	return True


if __name__ == '__main__':
	m = MockTimerManager()
	m.add_timer(100, test_function, m, 'F1')
	m.add_timer(30, test_function, m, 'F2')
	m.add_terminator(5000)
	m.start()
