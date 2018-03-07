import logging
import dbus_systemcalc
import mock_gobject

# Override the logging set in dbus_systemcalc, now only warnings and errors
# will be logged. This reduces the output of the unit test to a few lines.
dbus_systemcalc.logger = logging.getLogger()


# Patch an alternative function to get the portal ID, because the original retrieves the ID by getting
# the MAC address of 'eth0' which may not be available.
dbus_systemcalc.get_vrm_portal_id = lambda: 'aabbccddeeff'
mock_gobject.patch_gobject(dbus_systemcalc.gobject)
