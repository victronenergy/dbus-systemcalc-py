SOURCEDIR = $(PWD)
VEDLIBDIR = $(PWD)/ext/velib_python
INSTALL_CMD = install
LIBDIR = $(bindir)/ext/velib_python

FILES = \
	$(SOURCEDIR)/dbus_systemcalc.py \
	$(SOURCEDIR)/sc_utils.py

DELEGATES = \
	$(SOURCEDIR)/delegates/base.py \
	$(SOURCEDIR)/delegates/buzzercontrol.py \
	$(SOURCEDIR)/delegates/dvcc.py \
	$(SOURCEDIR)/delegates/hubtype.py \
	$(SOURCEDIR)/delegates/__init__.py \
	$(SOURCEDIR)/delegates/lgbattery.py \
	$(SOURCEDIR)/delegates/relaystate.py \
	$(SOURCEDIR)/delegates/servicemapper.py \
	$(SOURCEDIR)/delegates/vebussocwriter.py \
	$(SOURCEDIR)/delegates/systemstate.py \
	$(SOURCEDIR)/delegates/batterysense.py \
	$(SOURCEDIR)/delegates/batterylife.py \
	$(SOURCEDIR)/delegates/schedule.py \
	$(SOURCEDIR)/delegates/sourcetimers.py \
	$(SOURCEDIR)/delegates/bydbattery.py \
	$(SOURCEDIR)/delegates/batterysettings.py \
	$(SOURCEDIR)/delegates/batterydata.py \
	$(SOURCEDIR)/delegates/gps.py

VEDLIB_FILES = \
	$(VEDLIBDIR)/logger.py \
	$(VEDLIBDIR)/ve_utils.py \
	$(VEDLIBDIR)/vedbus.py \
	$(VEDLIBDIR)/settingsdevice.py \
	$(VEDLIBDIR)/dbusmonitor.py

help:
	@echo "The following make targets are available"
	@echo " help - print this message"
	@echo " install - install everything"
	@echo " clean - remove temporary files"

install_delegates : $(DELEGATES)
	@if [ "$^" != "" ]; then \
		$(INSTALL_CMD) -d $(DESTDIR)$(bindir)/delegates; \
		$(INSTALL_CMD) -m 644 -t $(DESTDIR)$(bindir)/delegates $^; \
	fi

install_app : $(FILES)
	@if [ "$^" != "" ]; then \
		$(INSTALL_CMD) -m 755 -d $(DESTDIR)$(bindir); \
		$(INSTALL_CMD) -m 755 -t $(DESTDIR)$(bindir) $^; \
		echo installed $(DESTDIR)$(bindir)/$(notdir $^); \
	fi

install_velib_python: $(VEDLIB_FILES)
	@if [ "$^" != "" ]; then \
		$(INSTALL_CMD) -m 755 -d $(DESTDIR)$(LIBDIR); \
		$(INSTALL_CMD) -m 644 -t $(DESTDIR)$(LIBDIR) $^; \
		echo installed $(DESTDIR)$(LIBDIR)/$(notdir $^); \
	fi

clean: ;

install: install_velib_python install_app install_delegates

test:
	nosetests -v -w tests

testinstall:
	$(eval TMP := $(shell mktemp -d))
	$(MAKE) DESTDIR=$(TMP) install
	(cd $(TMP) && ./dbus_systemcalc.py --help > /dev/null)
	-rm -rf $(TMP)

.PHONY: help install_app install_velib_python install test
