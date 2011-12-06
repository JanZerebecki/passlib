"""passlib.utils.compat - python 2/3 compatibility helpers"""
#=============================================================================
# figure out what version we're running
#=============================================================================
from sys import version as pyver
PY3 = pyver >= (3,0)
PY_MAX_25 = pyver < (2,6) # py 2.5 or earlier
PY_MIN_32 = pyver >= (3,2) # py 3.2 or later

#=============================================================================
# the default exported vars
#=============================================================================
__all__ = [
    "u", "b",
    "irange", "srange", ##"lrange",
    "lmap",
    "iteritems",
]

#=============================================================================
# typing
#=============================================================================
def is_mapping(obj):
    # non-exhaustive check, enough to distinguish from lists, etc
    return hasattr(obj, "items")

if (3,0) <= pyver < (3,2):
    # callable isn't dead, it's just resting
    from collections import Callable
    def callable(obj):
        return isinstance(obj, Callable)
    __all__.append("callable")

#=============================================================================
# unicode / bytes helpers
#=============================================================================
if PY3:
    def u(s):
        return s
    def b(s):
        assert isinstance(s, str)
        return s.encode("latin-1")
    unicode = str
    __all__.append("unicode")
#    string_types = (str,)
else:
    def u(s):
        return s.decode("unicode_escape")
    def b(s):
        assert isinstance(s, str)
        return s
    if PY_MAX_25:
        bytes = str
        __all__.append("bytes")
#    string_types = (unicode,str)

#=============================================================================
# bytes-specific helpers
#=============================================================================
# bytes format

#=============================================================================
# iteration helpers
#
# irange - range iterator
# trange - immutable range sequence (list under py2, range object under py3)
# lrange - range list
#
# lmap - map to list
#=============================================================================
if PY3:
    irange = trange = range
    ##def lrange(*a,**k):
    ##    return list(range(*a,**k))

    def lmap(*a, **k):
        return list(map(*a,**k))
    # imap = map

else:
    irange = xrange
    trange = range
    ##lrange = range

    lmap = map
    # from itertools import imap

if PY3:
    def iteritems(d):
        return d.items()
else:
    def iteritems(d):
        return d.iteritems()

#=============================================================================
# eof
#=============================================================================