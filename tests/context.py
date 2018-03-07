import os
import sys

# Modify path so we can find our own packages
test_dir = os.path.dirname(__file__)
sys.path.insert(0, test_dir)
sys.path.insert(1, os.path.join(test_dir, '..', 'ext', 'velib_python', 'test'))
sys.path.insert(1, os.path.join(test_dir, '..', 'ext', 'velib_python'))
sys.path.insert(1, os.path.join(test_dir, '..'))
