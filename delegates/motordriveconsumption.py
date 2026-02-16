from math import radians, sin, cos, sqrt, atan2, exp, degrees
from delegates.base import SystemCalcDelegate

PREFIX = "/MotorDrive"

MINIMUM_SPEED_MS = 0.3  # Ignore movements slower than this (m/s)
MAXIMUM_SPEED_MS = 20.0  # Cap movements faster than this (m/s)
BEARING_CHECK_MINIMUM_DISTANCE_M = 1  # Minimum distance to check bearing continuity (m)
BEARING_CHECK_THRESHOLD_DEGREES = (
	45.0  # Minimum bearing change to consider discontinuity (degrees)
)
BEARING_CHECK_MAXIMUM_DT_MS = (
	10000  # Maximum time delta to consider bearing continuity (ms)
)

ENERGY_EMA_MS = 3000  # 3 s

WINDOW_SEGMENT_COUNT = 50
WINDOW_SEGMENT_SIZE_M = 37  # 50 segments of 37 m = 1.85 km = 1 nautical mile


class MotorDriveConsumption(SystemCalcDelegate):
	def __init__(self):
		super(MotorDriveConsumption, self).__init__()
		self.last_point = None
		self.last_bearing = None
		self.is_continuous = False
		self.power_ema = None
		self.current_ema = None

		self.consumption_window = [[0, 0, 0] for _ in range(WINDOW_SEGMENT_COUNT)]
		self.consumption_window_index = 0
		self.consumption_window_total_distance = 0.0
		self.consumption_window_total_energy_wh = 0.0
		self.consumption_window_total_energy_ah = 0.0

	def get_output(self):
		return [
			(PREFIX + "/ConsumptionWh", {"gettext": "%dWh/km"}),
			(PREFIX + "/ConsumptionAh", {"gettext": "%dAh/km"}),
		]

	def get_input(self):
		return [
			(
				"com.victronenergy.gps",
				[
					"/Position/Latitude",
					"/Position/Longitude",
					"/UtcTime",
				],
			)
		]

	def _calculate(self):
		point = {
			"utc_time": self._dbusmonitor.get_value(
				self._dbusservice["/GpsService"], "/UtcTime"
			),
			"latitude": self._dbusmonitor.get_value(
				self._dbusservice["/GpsService"], "/Position/Latitude"
			),
			"longitude": self._dbusmonitor.get_value(
				self._dbusservice["/GpsService"], "/Position/Longitude"
			),
		}
		power = self._dbusservice["/MotorDrive/Power"]
		current = self._dbusservice["/MotorDrive/Current"]

		if (
			power is None
			or current is None
			or point["utc_time"] is None
			or point["latitude"] is None
			or point["longitude"] is None
		):
			self.is_continuous = False
			self.last_point = None
			self.last_bearing = None
			self.power_ema = None
			self.current_ema = None
			return

		dt = (
			(point["utc_time"] - self.last_point["utc_time"] + 86400000) % 86400000
			if self.last_point is not None
			else 0
		)
		distance = self._calculate_distance_traveled(point, dt)
		energy_wh = self._calculate_energy_wh_used(power, dt)
		energy_ah = self._calculate_energy_ah_used(current, dt)
		if distance is not None:
			self._accumulate(distance, energy_wh, energy_ah)
		return False

	def _calculate_distance_traveled(self, point, dt):
		distance_clamped = None
		if self.last_point is not None:
			distance_raw = self._haversine_distance_in_meter(self.last_point, point)
			distance_min = MINIMUM_SPEED_MS * (dt / 1000.0)
			if distance_raw < distance_min:
				self.is_continuous = False
				return None
			distance_max = MAXIMUM_SPEED_MS * (dt / 1000.0)
			distance_clamped = min(distance_raw, distance_max)

			bearing = self._bearing_between_points(self.last_point, point)
			if self.last_bearing is not None:
				bearing_diff = abs(((bearing - self.last_bearing + 540) % 360) - 180)
				if (
					dt < BEARING_CHECK_MAXIMUM_DT_MS
					and distance_clamped >= BEARING_CHECK_MINIMUM_DISTANCE_M
					and bearing_diff >= BEARING_CHECK_THRESHOLD_DEGREES
				):
					self.is_continuous = False
					return None
			self.last_bearing = bearing
		self.last_point = point
		if self.is_continuous is False:
			self.is_continuous = True
			return None
		return distance_clamped

	def _calculate_energy_wh_used(self, power, dt):
		alpha = 1 - exp(-dt / ENERGY_EMA_MS)
		self.power_ema = (
			self.power_ema + alpha * (power - self.power_ema)
			if self.power_ema is not None
			else power
		)
		return self.power_ema * (dt / 1000.0) / 3600.0  # Wh

	def _calculate_energy_ah_used(self, current, dt):
		alpha = 1 - exp(-dt / ENERGY_EMA_MS)
		self.current_ema = (
			self.current_ema + alpha * (current - self.current_ema)
			if self.current_ema is not None
			else current
		)
		return self.current_ema * (dt / 1000.0) / 3600.0  # Ah

	def _accumulate(self, distance, energy_wh, energy_ah):
		self.consumption_window_total_distance += distance
		self.consumption_window_total_energy_wh += energy_wh
		self.consumption_window_total_energy_ah += energy_ah

		while distance > 0:
			segment_remaining_distance = (
				WINDOW_SEGMENT_SIZE_M
				- self.consumption_window[self.consumption_window_index][0]
			)
			if distance < segment_remaining_distance:
				# Fits in current segment
				self.consumption_window[self.consumption_window_index][0] += distance
				self.consumption_window[self.consumption_window_index][1] += energy_wh
				self.consumption_window[self.consumption_window_index][2] += energy_ah
				distance = 0
				energy_wh = 0
				energy_ah = 0
			else:
				# Fill up current segment and move to next
				ratio = segment_remaining_distance / distance
				self.consumption_window[self.consumption_window_index][
					0
				] += segment_remaining_distance
				self.consumption_window[self.consumption_window_index][1] += (
					energy_wh * ratio
				)
				self.consumption_window[self.consumption_window_index][2] += (
					energy_ah * ratio
				)
				distance -= segment_remaining_distance
				energy_wh -= energy_wh * ratio
				energy_ah -= energy_ah * ratio

				self.consumption_window_index = (
					self.consumption_window_index + 1
				) % WINDOW_SEGMENT_COUNT
				# Subtract the segment that is being overwritten
				self.consumption_window_total_distance -= self.consumption_window[
					self.consumption_window_index
				][0]
				self.consumption_window_total_energy_wh -= self.consumption_window[
					self.consumption_window_index
				][1]
				self.consumption_window_total_energy_ah -= self.consumption_window[
					self.consumption_window_index
				][2]
				# Reset the segment
				self.consumption_window[self.consumption_window_index][0] = 0
				self.consumption_window[self.consumption_window_index][1] = 0
				self.consumption_window[self.consumption_window_index][2] = 0

	def _haversine_distance_in_meter(self, point1, point2):
		R = 6371000  # Radius of the Earth in meters
		phi1 = radians(point1["latitude"])
		phi2 = radians(point2["latitude"])
		delta_phi = radians(point2["latitude"] - point1["latitude"])
		delta_lambda = radians(point2["longitude"] - point1["longitude"])

		a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
		c = 2 * atan2(sqrt(a), sqrt(1 - a))

		distance = R * c
		return distance

	def _bearing_between_points(self, point1, point2):
		phi1 = radians(point1["latitude"])
		phi2 = radians(point2["latitude"])
		lambda1 = radians(point1["longitude"])
		lambda2 = radians(point2["longitude"])

		y = sin(lambda2 - lambda1) * cos(phi2)
		x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(lambda2 - lambda1)
		bearing = atan2(y, x)
		bearing_degrees = (degrees(bearing) + 360) % 360
		return bearing_degrees

	def update_values(self, newvalues):
		self._calculate()
		if self.consumption_window_total_distance > 0:
			consumption_wh = self.consumption_window_total_energy_wh / (
				self.consumption_window_total_distance / 1000.0
			)  # Wh/km
			consumption_ah = self.consumption_window_total_energy_ah / (
				self.consumption_window_total_distance / 1000.0
			)  # Ah/km
			newvalues[PREFIX + "/ConsumptionWh"] = consumption_wh
			newvalues[PREFIX + "/ConsumptionAh"] = consumption_ah
		pass
