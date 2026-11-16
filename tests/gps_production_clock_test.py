"""Exercise the clock selected by production code, without replacing _get_time."""
import datetime
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import context


class GpsProductionClockTest(unittest.TestCase):
    def check_correction(self, correction):
        clocks = {"elapsed": 100.0, "wall": datetime.datetime(2026, 11, 16, 12)}

        class WallClock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return clocks["wall"]

        # Load a separate production class so other tests' _get_time overrides
        # cannot hide a return to wall-clock timing. Both clock APIs keep their
        # actual return types, including on the pre-fix implementation.
        spec = importlib.util.spec_from_file_location(
            "gps_production_clock", Path(__file__).resolve().parents[1] / "delegates/gps.py")
        module = importlib.util.module_from_spec(spec)
        with patch("time.monotonic", side_effect=lambda: clocks["elapsed"]), \
                patch("datetime.datetime", WallClock):
            spec.loader.exec_module(module)
            gps = module.Gps()
            speed = [5.0]
            gps._dbusservice = {"/GpsService": "com.victronenergy.gps.test"}
            gps._dbusmonitor = SimpleNamespace(get_value=lambda service, path: speed[0])
            values = {}
            gps.update_values(values)
            self.assertEqual(values["/GpsSpeed"], 5.0)
            clocks["elapsed"] += 1.0
            clocks["wall"] += datetime.timedelta(seconds=1 + correction)
            speed[0] = 6.0
            gps.update_values(values)
            self.assertAlmostEqual(values["/GpsSpeed"], 6.0 - math.exp(-1.0))

    def test_backwards_wall_clock_correction(self):
        self.check_correction(-3600)

    def test_forwards_wall_clock_correction(self):
        self.check_correction(3600)

    def test_normal_elapsed_interval(self):
        self.check_correction(0)


if __name__ == "__main__":
    unittest.main()
