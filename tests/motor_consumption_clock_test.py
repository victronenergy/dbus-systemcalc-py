import unittest
from types import SimpleNamespace
import context
import patches
from delegates.motordriveconsumption import MotorDriveConsumption


class MotorConsumptionClockTest(unittest.TestCase):
    def make(self, previous, current):
        model = MotorDriveConsumption()
        model.last_point = {'utc_time': previous, 'latitude': 0.0, 'longitude': 0.0}
        model.is_continuous = True
        values = {'/UtcTime': current, '/Position/Latitude': 0.00001, '/Position/Longitude': 0.0}
        model._dbusmonitor = SimpleNamespace(get_value=lambda service, path: values.get(path))
        model._dbusservice = {'/GpsService': 'gps', '/MotorDrive/Power': 360.0, '/MotorDrive/Current': 10.0}
        return model

    def test_backward_clock_rebases_instead_of_waiting_for_old_time(self):
        model = self.make(3600000, 1000)
        model._calculate()
        self.assertEqual(model.consumption_window_total_energy_wh, 0)
        self.assertEqual(model.consumption_window_total_distance, 0)
        self.assertEqual(model.last_point['utc_time'], 1000)
        for timestamp, latitude in [(2000, 0.00002), (3000, 0.00003)]:
            readings = {'/UtcTime': timestamp, '/Position/Latitude': latitude, '/Position/Longitude': 0.0}
            model._dbusmonitor.get_value = lambda service, path, data=readings: data.get(path)
            model._calculate()
        self.assertGreater(model.consumption_window_total_distance, 0)
        self.assertAlmostEqual(model.consumption_window_total_energy_wh, 0.1)

    def test_duplicate_timestamp_does_not_replace_the_position_baseline(self):
        model = self.make(2000, 2000)
        model._calculate()
        self.assertEqual(model.last_point['latitude'], 0.0)
        self.assertEqual(model.consumption_window_total_energy_wh, 0)

    def test_midnight_is_still_a_normal_forward_sample(self):
        model = self.make(86399000, 0)
        model._calculate()
        self.assertAlmostEqual(model.consumption_window_total_energy_wh, 0.1)
        self.assertGreater(model.consumption_window_total_distance, 0)


if __name__ == '__main__':
    unittest.main()
