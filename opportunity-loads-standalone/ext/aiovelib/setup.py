from setuptools import setup

setup(
	name="aiovelib",
	version="0.1",
	description="asyncio version of velib_python",
	long_description=open("README.md").read(),
	classifiers=[
		"Programming Language :: Python",
	],
	author='Izak Burger',
	author_email='iburger@victronenergy.com',
	url='https://github.com/victronenergy/aiovelib',
	license='MIT',
	packages = ["aiovelib"],
	install_requires=[
		'dbus-next',
	],
)
