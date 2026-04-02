# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

dbus-systemcalc-py aggregates data from multiple D-Bus services (battery monitors, solar chargers, inverters, etc.) and publishes calculated system-wide metrics on D-Bus as `com.victronenergy.system`. It's the central hub for system calculations in Victron's Venus OS platform.

## Build and Test Commands

```bash
# Run all tests
make test

# Test that the application installs and runs correctly
make testinstall

# Install to system
make install

# Run the application
python3 dbus_systemcalc.py --debug
```

### Running a Single Test

Tests use `nosetests`. To run a specific test file or test case:

```bash
# Run a specific test file
python3 -m nose -v tests/systemcalc_test.py

# Run a specific test class
python3 -m nose -v tests/systemcalc_test.py:TestSystemCalc

# Run a specific test method
python3 -m nose -v tests/systemcalc_test.py:TestSystemCalc.test_hub1
```

## Architecture

### Delegate Pattern

The application uses a delegate-based architecture where each functional area is isolated in its own module under `delegates/`. Each delegate inherits from `SystemCalcDelegate` (in `delegates/base.py`) and implements standardized lifecycle hooks:

- `get_input()` - D-Bus paths to monitor
- `get_output()` - D-Bus paths to publish
- `get_settings()` - Settings from com.victronenergy.settings
- `update_values()` - Called when monitored values change
- `device_added()`/`device_removed()` - Device lifecycle hooks

### Main Components

- `dbus_systemcalc.py` - Main application, defines D-Bus tree structure for monitoring
- `sc_utils.py` - Utility functions (`safeadd`, `safemax`, service naming helpers)
- `delegates/` - 23+ delegate modules for different system functions
- `ext/velib_python/` - Git submodule with D-Bus utilities (VeDbusService, DbusMonitor, etc.)

### Key Delegates

- `dvcc.py` - Dynamic Voltage/Current Control (BMS limit management)
- `dynamicess.py` - Energy Storage System control logic
- `batterylife.py` - Battery health management
- `batterysense.py` - Temperature sensor selection
- `acinput.py` - AC input source detection

## Testing Infrastructure

Tests use a mock infrastructure in `ext/velib_python/test/`:

- `MockDbusMonitor` - Simulates D-Bus monitoring
- `MockDbusService` - Simulates D-Bus services
- `MockSettingsDevice` - Simulates settings
- `mock_gobject.py` - Simulates GLib timers

Test base class (`tests/base.py`) provides helper methods:

```python
self._add_device(service, values)   # Add mock device
self._set_setting(path, value)      # Change settings
self._update_values(interval)       # Tick timer forward
self._check_values(expected)        # Verify D-Bus output values
```

## D-Bus Service Integration

**Input services monitored:**
- `com.victronenergy.battery` - Battery monitors
- `com.victronenergy.solarcharger` - Solar chargers
- `com.victronenergy.vebus` - Inverter/chargers
- `com.victronenergy.grid`, `genset`, `charger`, `inverter`, etc.

**Output service published:** `com.victronenergy.system`

## Dependencies

- Python 3
- `dbus-python` - D-Bus bindings
- `PyGObject` - GLib bindings
- D-Bus system bus
- `ext/velib_python` submodule (init with `git submodule update --init`)
