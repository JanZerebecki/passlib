"""passlib.handler - code for implementing handlers, and global registry for handlers"""
#=============================================================================
# imports
#=============================================================================
from __future__ import with_statement
# core
import logging; log = logging.getLogger(__name__)
import math
from warnings import warn
# site
# pkg
import passlib.exc as exc
from passlib.exc import MissingBackendError, PasslibConfigWarning, \
                        PasslibHashWarning
from passlib.ifc import PasswordHash
from passlib.registry import get_crypt_handler
from passlib.utils import classproperty, consteq, getrandstr, getrandbytes,\
                          BASE64_CHARS, HASH64_CHARS, rng, to_native_str, \
                          is_crypt_handler, to_unicode, deprecated_method, \
                          MAX_PASSWORD_SIZE
from passlib.utils.compat import join_byte_values, irange, u, native_string_types, \
                                 uascii_to_str, join_unicode, unicode, str_to_uascii, \
                                 join_unicode, unicode_or_bytes_types, PY2, int_types
# local
__all__ = [
    # helpers for implementing MCF handlers
    'parse_mc2',
    'parse_mc3',
    'render_mc2',
    'render_mc3',

    # framework for implementing handlers
    'GenericHandler',
        'StaticHandler',
        'HasUserContext',
        'HasRawChecksum',
        'HasManyIdents',
        'HasSalt',
        'HasRawSalt',
        'HasRounds',
        'HasManyBackends',

    # other helpers
    'PrefixWrapper',
]

#=============================================================================
# constants
#=============================================================================

# common salt_chars & checksum_chars values
# (BASE64_CHARS, HASH64_CHARS imported above)
PADDED_BASE64_CHARS = BASE64_CHARS + u("=")
HEX_CHARS = u("0123456789abcdefABCDEF")
UPPER_HEX_CHARS = u("0123456789ABCDEF")
LOWER_HEX_CHARS = u("0123456789abcdef")

# special byte string containing all possible byte values
# XXX: treated as singleton by some of the code for efficiency.
ALL_BYTE_VALUES = join_byte_values(irange(256))

# deprecated aliases - will be removed after passlib 1.8
H64_CHARS = HASH64_CHARS
B64_CHARS = BASE64_CHARS
PADDED_B64_CHARS = PADDED_BASE64_CHARS
UC_HEX_CHARS = UPPER_HEX_CHARS
LC_HEX_CHARS = LOWER_HEX_CHARS

#=============================================================================
# support functions
#=============================================================================
def _bitsize(count, chars):
    """helper for bitsize() methods"""
    if chars and count:
        import math
        return int(count * math.log(len(chars), 2))
    else:
        return 0

#=============================================================================
# parsing helpers
#=============================================================================
_UDOLLAR = u("$")
_UZERO = u("0")

def validate_secret(secret):
    """ensure secret has correct type & size"""
    if not isinstance(secret, unicode_or_bytes_types):
        raise exc.ExpectedStringError(secret, "secret")
    if len(secret) > MAX_PASSWORD_SIZE:
        raise exc.PasswordSizeError()

def to_unicode_for_identify(hash):
    """convert hash to unicode for identify method"""
    if isinstance(hash, unicode):
        return hash
    elif isinstance(hash, bytes):
        # try as utf-8, but if it fails, use foolproof latin-1,
        # since we don't really care about non-ascii chars
        # when running identify.
        try:
            return hash.decode("utf-8")
        except UnicodeDecodeError:
            return hash.decode("latin-1")
    else:
        raise exc.ExpectedStringError(hash, "hash")

def parse_mc2(hash, prefix, sep=_UDOLLAR, handler=None):
    """parse hash using 2-part modular crypt format.

    this expects a hash of the format :samp:`{prefix}{salt}[${checksum}]`,
    such as md5_crypt, and parses it into salt / checksum portions.

    :arg hash: the hash to parse (bytes or unicode)
    :arg prefix: the identifying prefix (unicode)
    :param sep: field separator (unicode, defaults to ``$``).
    :param handler: handler class to pass to error constructors.

    :returns:
        a ``(salt, chk | None)`` tuple.
    """
    # detect prefix
    hash = to_unicode(hash, "ascii", "hash")
    assert isinstance(prefix, unicode)
    if not hash.startswith(prefix):
        raise exc.InvalidHashError(handler)

    # parse 2-part hash or 1-part config string
    assert isinstance(sep, unicode)
    parts = hash[len(prefix):].split(sep)
    if len(parts) == 2:
        salt, chk = parts
        return salt, chk or None
    elif len(parts) == 1:
        return parts[0], None
    else:
        raise exc.MalformedHashError(handler)

def parse_mc3(hash, prefix, sep=_UDOLLAR, rounds_base=10,
              default_rounds=None, handler=None):
    """parse hash using 3-part modular crypt format.

    this expects a hash of the format :samp:`{prefix}[{rounds}]${salt}[${checksum}]`,
    such as sha1_crypt, and parses it into rounds / salt / checksum portions.
    tries to convert the rounds to an integer,
    and throws error if it has zero-padding.

    :arg hash: the hash to parse (bytes or unicode)
    :arg prefix: the identifying prefix (unicode)
    :param sep: field separator (unicode, defaults to ``$``).
    :param rounds_base:
        the numeric base the rounds are encoded in (defaults to base 10).
    :param default_rounds:
        the default rounds value to return if the rounds field was omitted.
        if this is ``None`` (the default), the rounds field is *required*.
    :param handler: handler class to pass to error constructors.

    :returns:
        a ``(rounds : int, salt, chk | None)`` tuple.
    """
    # detect prefix
    hash = to_unicode(hash, "ascii", "hash")
    assert isinstance(prefix, unicode)
    if not hash.startswith(prefix):
        raise exc.InvalidHashError(handler)

    # parse 3-part hash or 2-part config string
    assert isinstance(sep, unicode)
    parts = hash[len(prefix):].split(sep)
    if len(parts) == 3:
        rounds, salt, chk = parts
    elif len(parts) == 2:
        rounds, salt = parts
        chk = None
    else:
        raise exc.MalformedHashError(handler)

    # validate & parse rounds portion
    if rounds.startswith(_UZERO) and rounds != _UZERO:
        raise exc.ZeroPaddedRoundsError(handler)
    elif rounds:
        rounds = int(rounds, rounds_base)
    elif default_rounds is None:
        raise exc.MalformedHashError(handler, "empty rounds field")
    else:
        rounds = default_rounds

    # return result
    return rounds, salt, chk or None

def parse_mc3_long(hash, prefix, sep=_UDOLLAR, handler=None):
    """
    parse hash using 3-part modular crypt format,
    with complex settings string instead of simple rounds.
    otherwise works same as :func:`parse_mc3`
    """
    # detect prefix
    hash = to_unicode(hash, "ascii", "hash")
    assert isinstance(prefix, unicode)
    if not hash.startswith(prefix):
        raise exc.InvalidHashError(handler)

    # parse 3-part hash or 2-part config string
    assert isinstance(sep, unicode)
    parts = hash[len(prefix):].split(sep)
    if len(parts) == 3:
        return parts
    elif len(parts) == 2:
        settings, salt = parts
        return settings, salt, None
    else:
        raise exc.MalformedHashError(handler)

def parse_int(source, base=10, default=None, param="value", handler=None):
    """
    helper to parse an integer config field

    :arg source: unicode source string
    :param base: numeric base
    :param default: optional default if source is empty
    :param param: name of variable, for error msgs
    :param handler: handler class, for error msgs
    """
    if source.startswith(_UZERO) and source != _UZERO:
        raise exc.MalformedHashError(handler, "zero-padded %s field" % param)
    elif source:
        return int(source, base)
    elif default is None:
        raise exc.MalformedHashError(handler, "empty %s field" % param)
    else:
        return default

#=============================================================================
# formatting helpers
#=============================================================================
def render_mc2(ident, salt, checksum, sep=u("$")):
    """format hash using 2-part modular crypt format; inverse of parse_mc2()

    returns native string with format :samp:`{ident}{salt}[${checksum}]`,
    such as used by md5_crypt.

    :arg ident: identifier prefix (unicode)
    :arg salt: encoded salt (unicode)
    :arg checksum: encoded checksum (unicode or None)
    :param sep: separator char (unicode, defaults to ``$``)

    :returns:
        config or hash (native str)
    """
    if checksum:
        parts = [ident, salt, sep, checksum]
    else:
        parts = [ident, salt]
    return uascii_to_str(join_unicode(parts))

def render_mc3(ident, rounds, salt, checksum, sep=u("$"), rounds_base=10):
    """format hash using 3-part modular crypt format; inverse of parse_mc3()

    returns native string with format :samp:`{ident}[{rounds}$]{salt}[${checksum}]`,
    such as used by sha1_crypt.

    :arg ident: identifier prefix (unicode)
    :arg rounds: rounds field (int or None)
    :arg salt: encoded salt (unicode)
    :arg checksum: encoded checksum (unicode or None)
    :param sep: separator char (unicode, defaults to ``$``)
    :param rounds_base: base to encode rounds value (defaults to base 10)

    :returns:
        config or hash (native str)
    """
    if rounds is None:
        rounds = u('')
    elif rounds_base == 16:
        rounds = u("%x") % rounds
    else:
        assert rounds_base == 10
        rounds = unicode(rounds)
    if checksum:
        parts = [ident, rounds, sep, salt, sep, checksum]
    else:
        parts = [ident, rounds, sep, salt]
    return uascii_to_str(join_unicode(parts))

#=============================================================================
# MinimalHandler
#=============================================================================
class MinimalHandler(PasswordHash):
    """
    helper class for implementing hash handlers.
    provides nothing besides a base implementation of the .replace() subclass constructor.
    """
    #===================================================================
    # class attr
    #===================================================================

    #: private flag used by replace() constructor to detect if this is already a subclass.
    _configured = False

    #===================================================================
    # configuration interface
    #===================================================================

    @classmethod
    def replace(cls):
        # NOTE: this provides the base implementation, which takes care of
        #       creating the newly configured class. Mixins and subclasses
        #       should wrap this, and modify the returned class to suit their options.
        name = cls.__name__
        if not cls._configured:
            # TODO: straighten out class naming, repr, and .name attr
            name = "<customized %s hasher>" % name
        return type(name, (cls,), dict(__module__=cls.__module__, _configured=True))

    #===================================================================
    # eoc
    #===================================================================

#=============================================================================
# GenericHandler
#=============================================================================
class GenericHandler(MinimalHandler):
    """helper class for implementing hash handlers.

    GenericHandler-derived classes will have (at least) the following
    constructor options, though others may be added by mixins
    and by the class itself:

    :param checksum:
        this should contain the digest portion of a
        parsed hash (mainly provided when the constructor is called
        by :meth:`from_string()`).
        defaults to ``None``.

    :param use_defaults:
        If ``False`` (the default), a :exc:`TypeError` should be thrown
        if any settings required by the handler were not explicitly provided.

        If ``True``, the handler should attempt to provide a default for any
        missing values. This means generate missing salts, fill in default
        cost parameters, etc.

        This is typically only set to ``True`` when the constructor
        is called by :meth:`encrypt`, allowing user-provided values
        to be handled in a more permissive manner.

    :param relaxed:
        If ``False`` (the default), a :exc:`ValueError` should be thrown
        if any settings are out of bounds or otherwise invalid.

        If ``True``, they should be corrected if possible, and a warning
        issue. If not possible, only then should an error be raised.
        (e.g. under ``relaxed=True``, rounds values will be clamped
        to min/max rounds).

        This is mainly used when parsing the config strings of certain
        hashes, whose specifications implementations to be tolerant
        of incorrect values in salt strings.

    Class Attributes
    ================

    .. attribute:: ident

        [optional]
        If this attribute is filled in, the default :meth:`identify` method will use
        it as a identifying prefix that can be used to recognize instances of this handler's
        hash. Filling this out is recommended for speed.

        This should be a unicode str.

    .. attribute:: _hash_regex

        [optional]
        If this attribute is filled in, the default :meth:`identify` method
        will use it to recognize instances of the hash. If :attr:`ident`
        is specified, this will be ignored.

        This should be a unique regex object.

    .. attribute:: checksum_size

        [optional]
        Specifies the number of characters that should be expected in the checksum string.
        If omitted, no check will be performed.

    .. attribute:: checksum_chars

        [optional]
        A string listing all the characters allowed in the checksum string.
        If omitted, no check will be performed.

        This should be a unicode str.

    .. attribute:: _stub_checksum

        Placeholder checksum that will be used by genconfig()
        in lieu of actually generating a hash for the empty string.
        This should be a string of the same datatype as :attr:`checksum`.

    Instance Attributes
    ===================
    .. attribute:: checksum

        The checksum string provided to the constructor (after passing it
        through :meth:`_norm_checksum`).

    Required Subclass Methods
    =========================
    The following methods must be provided by handler subclass:

    .. automethod:: from_string
    .. automethod:: to_string
    .. automethod:: _calc_checksum

    Default Methods
    ===============
    The following methods have default implementations that should work for
    most cases, though they may be overridden if the hash subclass needs to:

    .. automethod:: _norm_checksum

    .. automethod:: genconfig
    .. automethod:: genhash
    .. automethod:: identify
    .. automethod:: encrypt
    .. automethod:: verify
    """

    #===================================================================
    # class attr
    #===================================================================
    # this must be provided by the actual class.
    setting_kwds = None

    # providing default since most classes don't use this at all.
    context_kwds = ()

    # optional prefix that uniquely identifies hash
    ident = None

    # optional regexp for recognizing hashes,
    # used by default identify() if .ident isn't specified.
    _hash_regex = None

    # if specified, _norm_checksum will require this length
    checksum_size = None

    # if specified, _norm_checksum() will validate this
    checksum_chars = None

    # private flag used by HasRawChecksum
    _checksum_is_bytes = False

    #===================================================================
    # instance attrs
    #===================================================================
    checksum = None # stores checksum
#    use_defaults = False # whether _norm_xxx() funcs should fill in defaults.
#    relaxed = False # when _norm_xxx() funcs should be strict about inputs

    #===================================================================
    # init
    #===================================================================
    def __init__(self, checksum=None, use_defaults=False, relaxed=False,
                 **kwds):
        self.use_defaults = use_defaults
        self.relaxed = relaxed
        super(GenericHandler, self).__init__(**kwds)
        self.checksum = self._norm_checksum(checksum)

    def _norm_checksum(self, checksum):
        """validates checksum keyword against class requirements,
        returns normalized version of checksum.
        """
        # NOTE: by default this code assumes checksum should be unicode.
        # For classes where the checksum is raw bytes, the HasRawChecksum sets
        # the _checksum_is_bytes flag which alters various code paths below.
        if checksum is None:
            return None

        # normalize to bytes / unicode
        raw = self._checksum_is_bytes
        if raw:
            # NOTE: no clear route to reasonbly convert unicode -> raw bytes,
            # so relaxed does nothing here
            if not isinstance(checksum, bytes):
                raise exc.ExpectedTypeError(checksum, "bytes", "checksum")

        elif not isinstance(checksum, unicode):
            if isinstance(checksum, bytes) and self.relaxed:
                warn("checksum should be unicode, not bytes",
                     PasslibHashWarning)
                checksum = checksum.decode("ascii")
            else:
                raise exc.ExpectedTypeError(checksum, "unicode", "checksum")

        # check size
        cc = self.checksum_size
        if cc and len(checksum) != cc:
            raise exc.ChecksumSizeError(self, raw=raw)

        # check charset
        if not raw:
            cs = self.checksum_chars
            if cs and any(c not in cs for c in checksum):
                raise ValueError("invalid characters in %s checksum" %
                                 (self.name,))

        return checksum

    #===================================================================
    # password hash api - formatting interface
    #===================================================================
    @classmethod
    def identify(cls, hash):
        # NOTE: subclasses may wish to use faster / simpler identify,
        # and raise value errors only when an invalid (but identifiable)
        # string is parsed
        hash = to_unicode_for_identify(hash)
        if not hash:
            return False

        # does class specify a known unique prefix to look for?
        ident = cls.ident
        if ident is not None:
            return hash.startswith(ident)

        # does class provide a regexp to use?
        pat = cls._hash_regex
        if pat is not None:
            return pat.match(hash) is not None

        # as fallback, try to parse hash, and see if we succeed.
        # inefficient, but works for most cases.
        try:
            cls.from_string(hash)
            return True
        except ValueError:
            return False

    @classmethod
    def from_string(cls, hash, **context): # pragma: no cover
        """return parsed instance from hash/configuration string

        :param \*\*context:
            context keywords to pass to constructor (if applicable).

        :raises ValueError: if hash is incorrectly formatted

        :returns:
            hash parsed into components,
            for formatting / calculating checksum.
        """
        raise NotImplementedError("%s must implement from_string()" % (cls,))

    def to_string(self): # pragma: no cover
        """render instance to hash or configuration string

        :returns:
            if :attr:`checksum` is set, should return full hash string.
            if not, should either return abbreviated configuration string,
            or fill in a stub checksum.

            should return native string type (ascii-bytes under python 2,
            unicode under python 3)
        """
        # NOTE: documenting some non-standardized but common kwd flags
        #       that passlib to_string() method may have:
        #
        #       withchk=True -- if false, omit checksum portion of hash
        #
        raise NotImplementedError("%s must implement from_string()" %
                                  (self.__class__,))

    ##def to_config_string(self):
    ##    "helper for generating configuration string (ignoring hash)"
    ##    orig = self.checksum
    ##    try:
    ##        self.checksum = None
    ##        return self.to_string()
    ##    finally:
    ##            self.checksum = orig

    #===================================================================
    # checksum generation
    #===================================================================

    # NOTE: this is only used by genconfig(), and will be removed in passlib 2.0
    @property
    def _stub_checksum(self):
        """
        placeholder used by default .genconfig() so it can avoid expense of calculating digest.
        """
        # used fixed string if available
        if self.checksum_size:
            if self._checksum_is_bytes:
                return b'\x00' * self.checksum_size
            if self.checksum_chars:
                return self.checksum_chars[0] * self.checksum_size

        # hack to minimize cost of calculating real checksum
        if isinstance(self, HasRounds):
            orig = self.rounds
            self.rounds = self.min_rounds or 1
            try:
                return self._calc_checksum("")
            finally:
                self.rounds = orig

        # final fallback, generate a real checksum
        return self._calc_checksum("")

    def _calc_checksum(self, secret): # pragma: no cover
        """given secret; calcuate and return encoded checksum portion of hash
        string, taking config from object state

        calc checksum implementations may assume secret is always
        either unicode or bytes, checks are performed by verify/etc.
        """
        raise NotImplementedError("%s must implement _calc_checksum()" %
                                  (self.__class__,))

    #===================================================================
    #'application' interface (default implementation)
    #===================================================================

    @classmethod
    def hash(cls, secret, **kwds):
        validate_secret(secret)
        self = cls(use_defaults=True, **kwds)
        self.checksum = self._calc_checksum(secret)
        return self.to_string()

    @classmethod
    def verify(cls, secret, hash, **context):
        # NOTE: classes with multiple checksum encodings should either
        # override this method, or ensure that from_string() / _norm_checksum()
        # ensures .checksum always uses a single canonical representation.
        validate_secret(secret)
        self = cls.from_string(hash, **context)
        chk = self.checksum
        if chk is None:
            raise exc.MissingDigestError(cls)
        return consteq(self._calc_checksum(secret), chk)

    #===================================================================
    # legacy crypt interface
    #===================================================================

    @deprecated_method(deprecated="1.7", removed="2.0")
    @classmethod
    def genconfig(cls, **settings):
        # NOTE: this uses optional stub checksum to bypass potentially expensive digest generation,
        #       when caller just wants the config string.
        self = cls(use_defaults=True, **settings)
        self.checksum = self._stub_checksum
        return self.to_string()

    @deprecated_method(deprecated="1.7", removed="2.0")
    @classmethod
    def genhash(cls, secret, config, **context):
        if config is None:
            raise TypeError("config must be string")
        validate_secret(secret)
        self = cls.from_string(config, **context)
        self.checksum = self._calc_checksum(secret)
        return self.to_string()

    #===================================================================
    # migration interface (basde implementation)
    #===================================================================

    @classmethod
    def needs_update(cls, hash, secret=None, **kwds):
        # NOTE: subclasses should generally just wrap _calc_needs_update()
        #       to check their particular keywords.
        self = cls.from_string(hash)
        assert isinstance(self, cls)
        return self._calc_needs_update(secret=secret, **kwds)

    def _calc_needs_update(self, secret=None):
        """
        internal helper for :meth:`needs_update`.
        """
        # NOTE: this just provides a stub, subclasses & mixins
        #       should override this with their own tests.
        return False

    #===================================================================
    # experimental - the following methods are not finished or tested,
    # but way work correctly for some hashes
    #===================================================================
    _unparsed_settings = ("salt_size", "relaxed")
    _unsafe_settings = ("salt", "checksum")

    @classproperty
    def _parsed_settings(cls):
        return (key for key in cls.setting_kwds
                if key not in cls._unparsed_settings)

    # XXX: make this a global function?
    @staticmethod
    def _sanitize(value, char=u("*")):
        """default method to obscure sensitive fields"""
        if value is None:
            return None
        if isinstance(value, bytes):
            from passlib.utils import ab64_encode
            value = ab64_encode(value).decode("ascii")
        elif not isinstance(value, unicode):
            value = unicode(value)
        size = len(value)
        clip = min(4, size//8)
        return value[:clip] + char * (size-clip)

    @classmethod
    def parsehash(cls, hash, checksum=True, sanitize=False):
        """[experimental method] parse hash into dictionary of settings.

        this essentially acts as the inverse of :meth:`encrypt`: for most
        cases, if ``hash = cls.hash(secret, **opts)``, then
        ``cls.parsehash(hash)`` will return a dict matching the original options
        (with the extra keyword *checksum*).

        this method may not work correctly for all hashes,
        and may not be available on some few. its interface may
        change in future releases, if it's kept around at all.

        :arg hash: hash to parse
        :param checksum: include checksum keyword? (defaults to True)
        :param sanitize: mask data for sensitive fields? (defaults to False)
        """
        # FIXME: this may not work for hashes with non-standard settings.
        # XXX: how should this handle checksum/salt encoding?
        # need to work that out for encrypt anyways.
        self = cls.from_string(hash)
        # XXX: could split next few lines out as self._parsehash() for subclassing
        # XXX: could try to resolve ident/variant to publically suitable alias.
        UNSET = object()
        kwds = dict((key, getattr(self, key)) for key in self._parsed_settings
                    if getattr(self, key) != getattr(cls, key, UNSET))
        if checksum and self.checksum is not None:
            kwds['checksum'] = self.checksum
        if sanitize:
            if sanitize is True:
                sanitize = cls._sanitize
            for key in cls._unsafe_settings:
                if key in kwds:
                    kwds[key] = sanitize(kwds[key])
        return kwds

    @classmethod
    def bitsize(cls, **kwds):
        """[experimental method] return info about bitsizes of hash"""
        try:
            info = super(GenericHandler, cls).bitsize(**kwds)
        except AttributeError:
            info = {}
        cc = ALL_BYTE_VALUES if cls._checksum_is_bytes else cls.checksum_chars
        if cls.checksum_size and cc:
            # FIXME: this may overestimate size due to padding bits (e.g. bcrypt)
            # FIXME: this will be off by 1 for case-insensitive hashes.
            info['checksum'] = _bitsize(cls.checksum_size, cc)
        return info

    #===================================================================
    # eoc
    #===================================================================

class StaticHandler(GenericHandler):
    """GenericHandler mixin for classes which have no settings.

    This mixin assumes the entirety of the hash ise stored in the
    :attr:`checksum` attribute; that the hash has no rounds, salt,
    etc. This class provides the following:

    * a default :meth:`genconfig` that always returns None.
    * a default :meth:`from_string` and :meth:`to_string`
      that store the entire hash within :attr:`checksum`,
      after optionally stripping a constant prefix.

    All that is required by subclasses is an implementation of
    the :meth:`_calc_checksum` method.
    """
    # TODO: document _norm_hash()

    setting_kwds = ()

    # optional constant prefix subclasses can specify
    _hash_prefix = u("")

    @classmethod
    def from_string(cls, hash, **context):
        # default from_string() which strips optional prefix,
        # and passes rest unchanged as checksum value.
        hash = to_unicode(hash, "ascii", "hash")
        hash = cls._norm_hash(hash)
        # could enable this for extra strictness
        ##pat = cls._hash_regex
        ##if pat and pat.match(hash) is None:
        ##    raise ValueError("not a valid %s hash" % (cls.name,))
        prefix = cls._hash_prefix
        if prefix:
            if hash.startswith(prefix):
                hash = hash[len(prefix):]
            else:
                raise exc.InvalidHashError(cls)
        return cls(checksum=hash, **context)

    @classmethod
    def _norm_hash(cls, hash):
        """helper for subclasses to normalize case if needed"""
        return hash

    def to_string(self):
        assert self.checksum is not None
        return uascii_to_str(self._hash_prefix + self.checksum)

    # per-subclass: stores dynamically created subclass used by _calc_checksum() stub
    __cc_compat_hack = None

    def _calc_checksum(self, secret):
        """given secret; calcuate and return encoded checksum portion of hash
        string, taking config from object state
        """
        # NOTE: prior to 1.6, StaticHandler required classes implement genhash
        # instead of this method. so if we reach here, we try calling genhash.
        # if that succeeds, we issue deprecation warning. if it fails,
        # we'll just recurse back to here, but in a different instance.
        # so before we call genhash, we create a subclass which handles
        # throwing the NotImplementedError.
        cls = self.__class__
        assert cls.__module__ != __name__
        wrapper_cls = cls.__cc_compat_hack
        if wrapper_cls is None:
            def inner(self, secret):
                raise NotImplementedError("%s must implement _calc_checksum()" %
                                          (cls,))
            wrapper_cls = cls.__cc_compat_hack = type(cls.__name__ + "_wrapper",
                  (cls,), dict(_calc_checksum=inner, __module__=cls.__module__))
        context = dict((k,getattr(self,k)) for k in self.context_kwds)
        # NOTE: passing 'config=None' here even though not currently allowed by ifc,
        #       since it *is* allowed under the old 1.5 ifc we're checking for here.
        try:
            hash = wrapper_cls.genhash(secret, None, **context)
        except TypeError as err:
            if str(err) == "config must be string":
                raise NotImplementedError("%s must implement _calc_checksum()" %
                                          (cls,))
            else:
                raise
        warn("%r should be updated to implement StaticHandler._calc_checksum() "
             "instead of StaticHandler.genhash(), support for the latter "
             "style will be removed in Passlib 1.8" % cls,
             DeprecationWarning)
        return str_to_uascii(hash)

#=============================================================================
# GenericHandler mixin classes
#=============================================================================
class HasEncodingContext(GenericHandler):
    """helper for classes which require knowledge of the encoding used"""
    context_kwds = ("encoding",)
    default_encoding = "utf-8"

    def __init__(self, encoding=None, **kwds):
        super(HasEncodingContext, self).__init__(**kwds)
        self.encoding = encoding or self.default_encoding

class HasUserContext(GenericHandler):
    """helper for classes which require a user context keyword"""
    context_kwds = ("user",)

    def __init__(self, user=None, **kwds):
        super(HasUserContext, self).__init__(**kwds)
        self.user = user

    # XXX: would like to validate user input here, but calls to from_string()
    # which lack context keywords would then fail; so leaving code per-handler.

    # wrap funcs to accept 'user' as positional arg for ease of use.
    @classmethod
    def hash(cls, secret, user=None, **context):
        return super(HasUserContext, cls).hash(secret, user=user, **context)

    @classmethod
    def verify(cls, secret, hash, user=None, **context):
        return super(HasUserContext, cls).verify(secret, hash, user=user, **context)

    @deprecated_method(deprecated="1.7", removed="2.0")
    @classmethod
    def genhash(cls, secret, config, user=None, **context):
        return super(HasUserContext, cls).genhash(secret, config, user=user, **context)

    # XXX: how to guess the entropy of a username?
    #      most of these hashes are for a system (e.g. Oracle)
    #      which has a few *very common* names and thus really low entropy;
    #      while the rest are slightly less predictable.
    #      need to find good reference about this.
    ##@classmethod
    ##def bitsize(cls, **kwds):
    ##    info = super(HasUserContext, cls).bitsize(**kwds)
    ##    info['user'] = xxx
    ##    return info

#------------------------------------------------------------------------
# checksum mixins
#------------------------------------------------------------------------
class HasRawChecksum(GenericHandler):
    """mixin for classes which work with decoded checksum bytes

    .. todo::

        document this class's usage
    """
    # NOTE: GenericHandler.checksum_chars is ignored by this implementation.

    # NOTE: all HasRawChecksum code is currently part of GenericHandler,
    # using private '_checksum_is_bytes' flag.
    # this arrangement may be changed in the future.
    _checksum_is_bytes = True

#------------------------------------------------------------------------
# ident mixins
#------------------------------------------------------------------------
class HasManyIdents(GenericHandler):
    """mixin for hashes which use multiple prefix identifiers

    For the hashes which may use multiple identifier prefixes,
    this mixin adds an ``ident`` keyword to constructor.
    Any value provided is passed through the :meth:`norm_idents` method,
    which takes care of validating the identifier,
    as well as allowing aliases for easier specification
    of the identifiers by the user.

    .. todo::

        document this class's usage

    Class Methods
    =============
    .. todo:: document replace() and needs_update() options
    """

    #===================================================================
    # class attrs
    #===================================================================
    default_ident = None # should be unicode
    ident_values = None # should be list of unicode strings
    ident_aliases = None # should be dict of unicode -> unicode
        # NOTE: any aliases provided to norm_ident() as bytes
        #       will have been converted to unicode before
        #       comparing against this dictionary.

        # NOTE: relying on test_06_HasManyIdents() to verify
        #       these are configured correctly.

    #===================================================================
    # instance attrs
    #===================================================================
    ident = None

    #===================================================================
    # variant constructor
    #===================================================================
    @classmethod
    def replace(cls,  # keyword only...
              default_ident=None, ident=None, **kwds):
        """
        This mixin adds support for the following :meth:`~passlib.ifc.PasswordHash.replace` keywords:

        :param default_ident:
            default identifier that will be used by resulting customized hasher.

        :param ident:
            supported as alternate alias for **default_ident**.
        """
        # resolve aliases
        if ident is not None:
            if default_ident is not None:
                raise TypeError("'default_ident' and 'ident' are mutually exclusive")
            default_ident = ident

        # create subclass
        subcls = super(HasManyIdents, cls).replace(**kwds)

        # add custom default ident
        # (NOTE: creates instance to run value through _norm_ident())
        if default_ident is not None:
            subcls.default_ident = cls(ident=default_ident, use_defaults=True).ident
        return subcls

    #===================================================================
    # init
    #===================================================================
    def __init__(self, ident=None, **kwds):
        super(HasManyIdents, self).__init__(**kwds)
        self.ident = self._norm_ident(ident)

    def _norm_ident(self, ident):
        """
        helper which normalizes & validates 'ident' value.
        """
        # fill in default_ident if needed
        if ident is None:
            if not self.use_defaults:
                raise TypeError("no ident specified")
            ident = self.default_ident
            assert ident is not None, "class must define default_ident"

        # handle bytes
        assert ident is not None
        if isinstance(ident, bytes):
            ident = ident.decode('ascii')

        # check if identifier is valid
        iv = self.ident_values
        if ident in iv:
            return ident

        # resolve aliases, and recheck against ident_values
        ia = self.ident_aliases
        if ia:
            try:
                value = ia[ident]
            except KeyError:
                pass
            else:
                if value in iv:
                    return value

        # failure!
        raise ValueError("invalid ident: %r" % (ident,))

    #===================================================================
    # password hash api
    #===================================================================
    @classmethod
    def identify(cls, hash):
        hash = to_unicode_for_identify(hash)
        return any(hash.startswith(ident) for ident in cls.ident_values)

    @classmethod
    def _parse_ident(cls, hash):
        """extract ident prefix from hash, helper for subclasses' from_string()"""
        hash = to_unicode(hash, "ascii", "hash")
        for ident in cls.ident_values:
            if hash.startswith(ident):
                return ident, hash[len(ident):]
        raise exc.InvalidHashError(cls)

    # XXX: implement a needs_update() helper that marks everything but default_ident as deprecated?

    #===================================================================
    # eoc
    #===================================================================

#------------------------------------------------------------------------
# salt mixins
#------------------------------------------------------------------------
class HasSalt(GenericHandler):
    """mixin for validating salts.

    This :class:`GenericHandler` mixin adds a ``salt`` keyword to the class constuctor;
    any value provided is passed through the :meth:`_norm_salt` method,
    which takes care of validating salt length and content,
    as well as generating new salts if one it not provided.

    :param salt:
        optional salt string

    :param salt_size:
        optional size of salt (only used if no salt provided);
        defaults to :attr:`default_salt_size`.

    Class Attributes
    ================
    In order for :meth:`!_norm_salt` to do its job, the following
    attributes should be provided by the handler subclass:

    .. attribute:: min_salt_size

        The minimum number of characters allowed in a salt string.
        An :exc:`ValueError` will be throw if the provided salt is too small.
        Defaults to ``None``, for no minimum.

    .. attribute:: max_salt_size

        The maximum number of characters allowed in a salt string.
        By default an :exc:`ValueError` will be throw if the provided salt is
        too large; but if ``relaxed=True``, it will be clipped and a warning
        issued instead. Defaults to ``None``, for no maximum.

    .. attribute:: default_salt_size

        [required]
        If no salt is provided, this should specify the size of the salt
        that will be generated by :meth:`_generate_salt`. By default
        this will fall back to :attr:`max_salt_size`.

    .. attribute:: salt_chars

        A string containing all the characters which are allowed in the salt
        string. An :exc:`ValueError` will be throw if any other characters
        are encountered. May be set to ``None`` to skip this check (but see
        in :attr:`default_salt_chars`).

    .. attribute:: default_salt_chars

        [required]
        This attribute controls the set of characters use to generate
        *new* salt strings. By default, it mirrors :attr:`salt_chars`.
        If :attr:`!salt_chars` is ``None``, this attribute must be specified
        in order to generate new salts. Aside from that purpose,
        the main use of this attribute is for hashes which wish to generate
        salts from a restricted subset of :attr:`!salt_chars`; such as
        accepting all characters, but only using a-z.

    Instance Attributes
    ===================
    .. attribute:: salt

        This instance attribute will be filled in with the salt provided
        to the constructor (as adapted by :meth:`_norm_salt`)

    Subclassable Methods
    ====================
    .. automethod:: _norm_salt
    .. automethod:: _generate_salt
    """
    # TODO: document _truncate_salt()
    # XXX: allow providing raw salt to this class, and encoding it?

    #===================================================================
    # class attrs
    #===================================================================

    min_salt_size = None
    max_salt_size = None
    salt_chars = None

    @classproperty
    def default_salt_size(cls):
        """default salt size (defaults to *max_salt_size*)"""
        return cls.max_salt_size

    @classproperty
    def default_salt_chars(cls):
        """charset used to generate new salt strings (defaults to *salt_chars*)"""
        return cls.salt_chars

    # private helpers for HasRawSalt, shouldn't be used by subclasses
    _salt_is_bytes = False
    _salt_unit = "chars"

    # TODO: could support replace(min/max_desired_salt_size) via using() and needs_update()

    #===================================================================
    # instance attrs
    #===================================================================
    salt = None

    #===================================================================
    # variant constructor
    #===================================================================
    @classmethod
    def replace(cls, # keyword only...
              default_salt_size=None,
              salt_size=None, # aliases used by CryptContext
              **kwds):

        # check for aliases used by CryptContext
        if salt_size is not None:
            if default_salt_size is not None:
                raise TypeError("'salt_size' and 'default_salt_size' aliases are mutually exclusive")
            default_salt_size = salt_size

        # generate new subclass
        subcls = super(HasSalt, cls).replace(**kwds)

        # replace default_rounds
        if default_salt_size is not None:
            if isinstance(default_salt_size, native_string_types):
                default_salt_size = int(default_salt_size)
            subcls.default_salt_size = subcls._clip_to_valid_salt_size(default_salt_size,
                                                                       param="default_salt_size")
        return subcls

    # XXX: would like to combine w/ _norm_salt() code below, but doesn't quite fit.
    @classmethod
    def _clip_to_valid_salt_size(cls, salt_size, param="salt_size", relaxed=True):
        """
        internal helper --
        clip salt size value to handler's absolute limits (min_salt_size / max_salt_size)

        :param relaxed:
            if ``True`` (the default), issues PasslibHashWarning is rounds are outside allowed range.
            if ``False``, raises a ValueError instead.

        :param param:
            optional name of parameter to insert into error/warning messages.

        :returns:
            clipped rounds value
        """
        mn = cls.min_salt_size or 0
        mx = cls.max_salt_size

        # check if salt size is fixed
        if mn == mx:
            if salt_size != mn:
                msg = "%s: %s (%d) must be exactly %d" % (cls.name, param, salt_size, mn)
                if relaxed:
                    warn(msg, PasslibHashWarning)
                else:
                    raise ValueError(msg)
            return mn

        # check min size
        if salt_size < mn:
            msg = "%s: %s (%r) below min_salt_size (%d)" % (cls.name, param, salt_size, mn)
            if relaxed:
                warn(msg, PasslibHashWarning)
                salt_size = mn
            else:
                raise ValueError(msg)

        # check max size
        if mx and salt_size > mx:
            msg = "%s: %s (%r) above max_salt_size (%d)" % (cls.name, param, salt_size, mx)
            if relaxed:
                warn(msg, PasslibHashWarning)
                salt_size = mx
            else:
                raise ValueError(msg)

        return salt_size

    #===================================================================
    # init
    #===================================================================
    def __init__(self, salt=None, salt_size=None, **kwds):
        super(HasSalt, self).__init__(**kwds)
        self.salt = self._norm_salt(salt, salt_size=salt_size)

    def _norm_salt(self, salt, salt_size=None):
        """helper to normalize & validate user-provided salt string

        If no salt provided, a random salt is generated
        using :attr:`default_salt_size` and :attr:`default_salt_chars`.

        :arg salt: salt string or ``None``
        :param salt_size: optionally specified size of autogenerated salt

        :raises TypeError:
            If salt not provided and ``use_defaults=False``.

        :raises ValueError:

            * if salt contains chars that aren't in :attr:`salt_chars`.
            * if salt contains less than :attr:`min_salt_size` characters.
            * if ``relaxed=False`` and salt has more than :attr:`max_salt_size`
              characters (if ``relaxed=True``, the salt is truncated
              and a warning is issued instead).

        :returns:
            normalized or generated salt
        """
        # generate new salt if none provided
        if salt is None:
            if not self.use_defaults:
                raise TypeError("no salt specified")
            if salt_size is None:
                salt_size = self.default_salt_size
            salt = self._generate_salt(salt_size)

        # check type
        if self._salt_is_bytes:
            if not isinstance(salt, bytes):
                raise exc.ExpectedTypeError(salt, "bytes", "salt")
        else:
            if not isinstance(salt, unicode):
                # NOTE: allowing bytes under py2 so salt can be native str.
                if isinstance(salt, bytes) and (PY2 or self.relaxed):
                    salt = salt.decode("ascii")
                else:
                    raise exc.ExpectedTypeError(salt, "unicode", "salt")

            # check charset
            sc = self.salt_chars
            if sc is not None and any(c not in sc for c in salt):
                raise ValueError("invalid characters in %s salt" % self.name)

        # check min size
        mn = self.min_salt_size
        if mn and len(salt) < mn:
            msg = "salt too small (%s requires %s %d %s)" % (self.name,
                        "exactly" if mn == self.max_salt_size else ">=", mn,
                        self._salt_unit)
            raise ValueError(msg)

        # check max size
        mx = self.max_salt_size
        if mx and len(salt) > mx:
            msg = "salt too large (%s requires %s %d %s)" % (self.name,
                        "exactly" if mx == mn else "<=", mx, self._salt_unit)
            if self.relaxed:
                warn(msg, PasslibHashWarning)
                salt = self._truncate_salt(salt, mx)
            else:
                raise ValueError(msg)

        return salt

    @staticmethod
    def _truncate_salt(salt, mx):
        # NOTE: some hashes (e.g. bcrypt) has structure within their
        # salt string. this provides a method to override to perform
        # the truncation properly
        return salt[:mx]

    def _generate_salt(self, salt_size):
        """helper method for _norm_salt(); generates a new random salt string.

        :arg salt_size: salt size to generate
        """
        return getrandstr(rng, self.default_salt_chars, salt_size)

    @classmethod
    def bitsize(cls, salt_size=None, **kwds):
        """[experimental method] return info about bitsizes of hash"""
        info = super(HasSalt, cls).bitsize(**kwds)
        if salt_size is None:
            salt_size = cls.default_salt_size
        # FIXME: this may overestimate size due to padding bits
        # FIXME: this will be off by 1 for case-insensitive hashes.
        info['salt'] = _bitsize(salt_size, cls.default_salt_chars)
        return info

    #===================================================================
    # eoc
    #===================================================================

class HasRawSalt(HasSalt):
    """mixin for classes which use decoded salt parameter

    A variant of :class:`!HasSalt` which takes in decoded bytes instead of an encoded string.

    .. todo::

        document this class's usage
    """

    salt_chars = ALL_BYTE_VALUES

    # NOTE: all HasRawSalt code is currently part of HasSalt, using private
    # '_salt_is_bytes' flag. this arrangement may be changed in the future.
    _salt_is_bytes = True
    _salt_unit = "bytes"

    def _generate_salt(self, salt_size):
        assert self.salt_chars in [None, ALL_BYTE_VALUES]
        return getrandbytes(rng, salt_size)

#------------------------------------------------------------------------
# rounds mixin
#------------------------------------------------------------------------
class HasRounds(GenericHandler):
    """mixin for validating rounds parameter

    This :class:`GenericHandler` mixin adds a ``rounds`` keyword to the class
    constuctor; any value provided is passed through the :meth:`_norm_rounds`
    method, which takes care of validating the number of rounds.

    :param rounds: optional number of rounds hash should use

    Class Attributes
    ================
    In order for :meth:`!_norm_rounds` to do its job, the following
    attributes must be provided by the handler subclass:

    .. attribute:: min_rounds

        The minimum number of rounds allowed. A :exc:`ValueError` will be
        thrown if the rounds value is too small. Defaults to ``0``.

    .. attribute:: max_rounds

        The maximum number of rounds allowed. A :exc:`ValueError` will be
        thrown if the rounds value is larger than this. Defaults to ``None``
        which indicates no limit to the rounds value.

    .. attribute:: default_rounds

        If no rounds value is provided to constructor, this value will be used.
        If this is not specified, a rounds value *must* be specified by the
        application.

    .. attribute:: rounds_cost

        [required]
        The ``rounds`` parameter typically encodes a cpu-time cost
        for calculating a hash. This should be set to ``"linear"``
        (the default) or ``"log2"``, depending on how the rounds value relates
        to the actual amount of time that will be required.

    Class Methods
    =============
    .. todo:: document replace() and needs_update() options

    Instance Attributes
    ===================
    .. attribute:: rounds

        This instance attribute will be filled in with the rounds value provided
        to the constructor (as adapted by :meth:`_norm_rounds`)

    Subclassable Methods
    ====================
    .. automethod:: _norm_rounds
    """
    #===================================================================
    # class attrs
    #===================================================================

    #-----------------
    # algorithm options -- not application configurable
    #-----------------
    # XXX: rename to min_valid_rounds / max_valid_rounds,
    #      to clarify role compared to min_desired_rounds / max_desired_rounds?
    min_rounds = 0
    max_rounds = None
    rounds_cost = "linear" # default to the common case

    # hack to pass info to _CryptRecord
    using_rounds_kwds = ("min_desired_rounds", "max_desired_rounds",
                         "min_rounds", "max_rounds",
                         "default_rounds", "vary_rounds")

    #-----------------
    # desired & default rounds -- configurable via .replace() classmethod
    #-----------------
    min_desired_rounds = None
    max_desired_rounds = None
    default_rounds = None
    vary_rounds = None

    #===================================================================
    # instance attrs
    #===================================================================
    rounds = None

    #===================================================================
    # variant constructor
    #===================================================================
    @classmethod
    def replace(cls, # keyword only...
              min_desired_rounds=None, max_desired_rounds=None,
              default_rounds=None, vary_rounds=None,
              min_rounds=None, max_rounds=None, rounds=None,  # aliases used by CryptContext
              **kwds):

        # check for aliases used by CryptContext
        if min_rounds is not None:
            if min_desired_rounds is not None:
                raise TypeError("'min_rounds' and 'min_desired_rounds' aliases are mutually exclusive")
            min_desired_rounds = min_rounds

        if max_rounds is not None:
            if max_desired_rounds is not None:
                raise TypeError("'max_rounds' and 'max_desired_rounds' aliases are mutually exclusive")
            max_desired_rounds = max_rounds

        # use 'rounds' as fallback for min, max, AND default
        # XXX: would it be better to make 'default_rounds' and 'rounds'
        #      aliases, and have a separate 'require_rounds' parameter for this behavior?
        if rounds is not None:
            if min_desired_rounds is None:
                min_desired_rounds = rounds
            if max_desired_rounds is None:
                max_desired_rounds = rounds
            if default_rounds is None:
                default_rounds = rounds

        # generate new subclass
        subcls = super(HasRounds, cls).replace(**kwds)

        # replace min_desired_rounds
        if min_desired_rounds is None:
            explicit_min_rounds = False
            min_desired_rounds = cls.min_desired_rounds
        else:
            explicit_min_rounds = True
            if isinstance(min_desired_rounds, native_string_types):
                min_desired_rounds = int(min_desired_rounds)
            if min_desired_rounds < 0:
                raise ValueError("%s: min_desired_rounds (%r) below 0" %
                                 (subcls.name, min_desired_rounds))
            subcls.min_desired_rounds = subcls._clip_to_valid_rounds(min_desired_rounds,
                                                                     param="min_desired_rounds")

        # replace max_desired_rounds
        if max_desired_rounds is None:
            max_desired_rounds = cls.max_desired_rounds
        else:
            if isinstance(max_desired_rounds, native_string_types):
                max_desired_rounds = int(max_desired_rounds)
            if min_desired_rounds and max_desired_rounds < min_desired_rounds:
                msg = "%s: max_desired_rounds (%r) below min_desired_rounds (%r)" % \
                      (subcls.name, max_desired_rounds, min_desired_rounds)
                if explicit_min_rounds:
                    raise ValueError(msg)
                else:
                    warn(msg, PasslibConfigWarning)
                    max_desired_rounds = min_desired_rounds
            elif max_desired_rounds < 0:
                raise ValueError("%s: max_desired_rounds (%r) below 0" %
                                 (subcls.name, max_desired_rounds))
            subcls.max_desired_rounds = subcls._clip_to_valid_rounds(max_desired_rounds,
                                                                     param="max_desired_rounds")

        # replace default_rounds
        if default_rounds is not None:
            if isinstance(default_rounds, native_string_types):
                default_rounds = int(default_rounds)
            if min_desired_rounds and default_rounds < min_desired_rounds:
                raise ValueError("%s: default_rounds (%r) below min_desired_rounds (%r)" %
                                 (subcls.name, default_rounds, min_desired_rounds))
            elif max_desired_rounds and default_rounds > max_desired_rounds:
                raise ValueError("%s: default_rounds (%r) above max_desired_rounds (%r)" %
                                 (subcls.name, default_rounds, max_desired_rounds))
            subcls.default_rounds = subcls._clip_to_valid_rounds(default_rounds,
                                                                 param="default_rounds")

        # clip default rounds to new limits.
        if subcls.default_rounds is not None:
            subcls.default_rounds = subcls._clip_to_desired_rounds(subcls.default_rounds)

        # replace / set vary_rounds
        if vary_rounds is not None:
            if isinstance(vary_rounds, native_string_types):
                if vary_rounds.endswith("%"):
                    vary_rounds = float(vary_rounds[:-1]) * 0.01
                elif "." in vary_rounds:
                    vary_rounds = float(vary_rounds)
                else:
                    vary_rounds = int(vary_rounds)
            if vary_rounds < 0:
                raise ValueError("%s: vary_rounds (%r) below 0" %
                                 (subcls.name, vary_rounds))
            elif isinstance(vary_rounds, float):
                # TODO: deprecate / disallow vary_rounds=1.0
                if vary_rounds > 1:
                    raise ValueError("%s: vary_rounds (%r) above 1.0" %
                                     (subcls.name, vary_rounds))
            elif not isinstance(vary_rounds, int):
                raise TypeError("vary_rounds must be int or float")
            if vary_rounds:
                warn("The 'vary_rounds' option is deprecated as of Passlib 1.7, "
                     "and will be removed in Passlib 2.0", PasslibConfigWarning)
            subcls.vary_rounds = vary_rounds
            # XXX: could cache _calc_vary_rounds_range() here if needed,
            #      but would need to handle user manually changing .default_rounds
        return subcls

    @classmethod
    def _clip_to_valid_rounds(cls, rounds, param="rounds", relaxed=True):
        """
        internal helper --
        clip rounds value to handler's absolute limits (min_rounds / max_rounds)

        :param relaxed:
            if ``True`` (the default), issues PasslibHashWarning is rounds are outside allowed range.
            if ``False``, raises a ValueError instead.

        :param param:
            optional name of parameter to insert into error/warning messages.

        :returns:
            clipped rounds value
        """
        # check minimum
        mn = cls.min_rounds
        if rounds < mn:
            msg = "%s: %s (%r) below min_rounds (%d)" % (cls.name, param, rounds, mn)
            if relaxed:
                warn(msg, PasslibHashWarning)
                rounds = mn
            else:
                raise ValueError(msg)

        # check maximum
        mx = cls.max_rounds
        if mx and rounds > mx:
            msg = "%s: %s (%r) above max_rounds (%d)" % (cls.name, param, rounds, mx)
            if relaxed:
                warn(msg, PasslibHashWarning)
                rounds = mx
            else:
                raise ValueError(msg)

        return rounds

    @classmethod
    def _clip_to_desired_rounds(cls, rounds):
        """
        helper for :meth:`_generate_rounds` --
        clips rounds value to desired min/max set by class (if any)
        """
        # NOTE: min/max_desired_rounds are None if unset.
        # check minimum
        mnd = cls.min_desired_rounds or 0
        if rounds < mnd:
            return mnd

        # check maximum
        mxd = cls.max_desired_rounds
        if mxd and rounds > mxd:
            return mxd

        return rounds

    @classmethod
    def _calc_vary_rounds_range(cls, default_rounds):
        """
        helper for :meth:`_generate_rounds` --
        returns range for vary rounds generation.

        :returns:
            (lower, upper) limits suitable for random.randint()
        """
        # XXX: could precalculate output of this in replace() method, and save per-hash cost.
        #      but then users patching cls.vary_rounds / cls.default_rounds would get wrong value.
        assert default_rounds
        vary_rounds = cls.vary_rounds

        # if vary_rounds specified as % of default, convert it to actual rounds
        def linear_to_native(value, upper):
            return value
        if isinstance(vary_rounds, float):
            assert 0 <= vary_rounds <= 1 # TODO: deprecate vary_rounds==1
            if cls.rounds_cost == "log2":
                # special case -- have to convert default_rounds to linear scale,
                # apply +/- vary_rounds to that, and convert back to log scale again.
                # linear_to_native() takes care of the "convert back" step.
                default_rounds = 1 << default_rounds
                def linear_to_native(value, upper):
                    if value <= 0: # log() undefined for <= 0
                        return 0
                    elif upper: # use smallest upper bound for start of range
                        return int(math.log(value, 2))
                    else: # use greatest lower bound for end of range
                        return int(math.ceil(math.log(value, 2)))
            # calculate integer vary rounds based on current default_rounds
            vary_rounds = int(default_rounds * vary_rounds)

        # calculate bounds based on default_rounds +/- vary_rounds
        assert vary_rounds >= 0 and isinstance(vary_rounds, int_types)
        lower = linear_to_native(default_rounds - vary_rounds, False)
        upper = linear_to_native(default_rounds + vary_rounds, True)
        return cls._clip_to_desired_rounds(lower), cls._clip_to_desired_rounds(upper)

    #===================================================================
    # init
    #===================================================================
    def __init__(self, rounds=None, **kwds):
        super(HasRounds, self).__init__(**kwds)
        self.rounds = self._norm_rounds(rounds)

    def _norm_rounds(self, rounds):
        """
        helper for normalizing rounds value.

        :arg rounds: ``None``, or an integer cost parameter.

        :raises TypeError:
            * if ``use_defaults=False`` and no rounds is specified
            * if rounds is not an integer.

        :raises ValueError:

            * if rounds is ``None`` and class does not specify a value for
              :attr:`default_rounds`.
            * if ``relaxed=False`` and rounds is outside bounds of
              :attr:`min_rounds` and :attr:`max_rounds` (if ``relaxed=True``,
              the rounds value will be clamped, and a warning issued).

        :returns:
            normalized rounds value
        """

        # init rounds attr, using default_rounds (etc) if needed
        explicit = False
        if rounds is None:
            if not self.use_defaults:
                raise TypeError("no rounds specified")
            rounds = self._generate_rounds()  # NOTE: will throw ValueError if default not set
            assert isinstance(rounds, int_types)
        elif self.use_defaults:
            # warn if rounds is outside desired bounds only if user provided explicit rounds
            # to .hash() -- hence the .use_defaults check, which will be false if we're
            # coming from .verify() / .genhash()
            explicit = True

        # check type
        if not isinstance(rounds, int_types):
            raise exc.ExpectedTypeError(rounds, "integer", "rounds")

        # check valid bounds
        rounds = self._clip_to_valid_rounds(rounds, relaxed=self.relaxed)

        # if rounds explicitly specified, warn if outside desired bounds, but use it
        if explicit:
            mnd = self.min_desired_rounds
            if mnd and rounds < mnd:
                warn("using rounds value (%r) below desired minimum (%d)" % (rounds, mnd),
                     exc.PasslibConfigWarning)

            mxd = self.max_desired_rounds
            if mxd and rounds > mxd:
                warn("using rounds value (%r) above desired maximum (%d)" % (rounds, mxd),
                     exc.PasslibConfigWarning)
        return rounds

    def _generate_rounds(self):
        """
        internal helper for :meth:`_norm_rounds` --
        returns default rounds value, incorporating vary_rounds,
        and any other limitations hash may place on rounds parameter.
        """
        # load default rounds
        rounds = self.default_rounds
        if rounds is None:
            raise TypeError("%s rounds value must be specified explicitly" % (self.name,))

        # randomly vary the rounds slightly basic on vary_rounds parameter.
        # reads default_rounds internally.
        if self.vary_rounds:
            lower, upper = self._calc_vary_rounds_range(rounds)
            assert lower <= rounds <= upper
            if lower < upper:
                rounds = rng.randint(lower, upper)

        return rounds

    #===================================================================
    # migration interface
    #===================================================================
    def _calc_needs_update(self, **kwds):
        """
        mark hash as needing update if rounds is outside desired bounds.
        """
        min_desired_rounds = self.min_desired_rounds
        if min_desired_rounds and self.rounds < min_desired_rounds:
            return True
        max_desired_rounds = self.max_desired_rounds
        if max_desired_rounds and self.rounds > max_desired_rounds:
            return True
        return super(HasRounds, self)._calc_needs_update(**kwds)

    #===================================================================
    # experimental methods
    #===================================================================
    @classmethod
    def bitsize(cls, rounds=None, vary_rounds=.1, **kwds):
        """[experimental method] return info about bitsizes of hash"""
        info = super(HasRounds, cls).bitsize(**kwds)
        # NOTE: this essentially estimates how many bits of "salt"
        # can be added by varying the rounds value just a little bit.
        if cls.rounds_cost != "log2":
            # assume rounds can be randomized within the range
            #     rounds*(1-vary_rounds) ... rounds*(1+vary_rounds)
            # then this can be used to encode
            #     log2(rounds*(1+vary_rounds)-rounds*(1-vary_rounds))
            # worth of salt-like bits. this works out to
            #     1+log2(rounds*vary_rounds)
            import math
            if rounds is None:
                rounds = cls.default_rounds
            info['rounds'] = max(0, int(1+math.log(rounds*vary_rounds,2)))
        ## else: # log2 rounds
            # all bits of the rounds value are critical to choosing
            # the time-cost, and can't be randomized.
        return info

    #===================================================================
    # eoc
    #===================================================================

#------------------------------------------------------------------------
# backend mixin & helpers
#------------------------------------------------------------------------
##def _clear_backend(cls):
##    "restore HasManyBackend subclass to unloaded state - used by unittests"
##    assert issubclass(cls, HasManyBackends) and cls is not HasManyBackends
##    if cls._backend:
##        del cls._backend
##        del cls._calc_checksum

class HasManyBackends(GenericHandler):
    """GenericHandler mixin which provides selecting from multiple backends.

    .. todo::

        finish documenting this class's usage

    For hashes which need to select from multiple backends,
    depending on the host environment, this class
    offers a way to specify alternate :meth:`_calc_checksum` methods,
    and will dynamically chose the best one at runtime.

    Public API
    ----------

    .. attribute:: backends

        This attribute should be a tuple containing the names of the backends
        which are supported. Two common names are ``"os_crypt"`` (if backend
        uses :mod:`crypt`), and ``"builtin"`` (if the backend is a pure-python
        fallback).

    .. automethod:: get_backend
    .. automethod:: set_backend
    .. automethod:: has_backend

    .. warning::

        :meth:`set_backend` and :meth:`has_backend` are intended to be called
        during application startup -- they affect global state, are not threadsafe.

    Private API (Subclass Hooks)
    ----------------------------
    The following attributes and methods should be filled in by the subclass
    which is using :class:`HasManyBackends` as a mixin:

    .. attribute:: _load_backend_{name}

        private class method that should try to load the specified backend,
        one of which should be provided for each backend listed in :attr:`backends`.

        * if backend isn't available, it should return ``None``.
        * if backend is available, it should return a callable
          which implements :meth:`_calc_checksum`.
        * it may also do things like import modules, run tests, issue warnings,
          etc; though it should avoid doing things which would change the operation
          of other backends (e.g. modify ``cls.default_rounds``).

        .. versionadded:: 1.7

        .. warning::

            Due to the way passlib's internals are arranged,
            backends should always store stateful data at the class level
            (not the module level), and be prepared to be called on subclasses
            which may be set to a different backend from their parent.

            Idempotent module-level data such as lazy imports are fine.

    .. attribute:: _has_backend_{name}

        private class attribute checked by :meth:`has_backend` to see if a
        specific backend is available, it should be either ``True``
        or ``False``. One of these should be provided by
        the subclass for each backend listed in :attr:`backends`.

        .. deprecated:: 1.7

            use :attr:`_load_backend_{name}` instead.
            support for this attribute will be removed in Passlib 2.0.

    .. classmethod:: _calc_checksum_{name}

        private class method that should implement :meth:`_calc_checksum`
        for a given backend. it will only be called if the backend has
        been selected by :meth:`set_backend`. One of these should be provided
        by the subclass for each backend listed in :attr:`backends`.

        .. deprecated:: 1.7

            use :attr:`_load_backend_{name}` instead.
            this attribute will be ignored in Passlib 2.0.
    """
    backends = None # list of backend names, provided by subclass.

    _backend = None # holds currently loaded backend (if any) or None

    #: optional class-specific text containing suggestion about what to do
    #: when no backends are available.
    _no_backend_suggestion = None

    @classmethod
    def get_backend(cls):
        """return name of currently active backend.

        if no backend has been loaded,
        loads and returns name of default backend.

        :raises passlib.exc.MissingBackendError: if no backends are available.

        :returns: name of active backend
        """
        if not cls._backend:
            cls.set_backend()
            assert cls._backend, "set_backend() failed to load a default backend"
        return cls._backend

    @classmethod
    def has_backend(cls, name="any"):
        """check if support is currently available for specified backend.

        :arg name:
            name of backend to check for.
            defaults to ``"any"``,
            but can be any string accepted by :meth:`set_backend`.

        :raises ValueError: if backend name is unknown

        :returns:
            ``True`` if backend is currently supported,
            ``False`` if it's not,
            and ``None`` if it's present, but won't load due to a security issue.
        """
        if name == "any" or name == "default":
            if cls._backend:
                return True
            try:
                cls.set_backend()
                return True
            except exc.PasslibSecurityError:
                return None
            except exc.MissingBackendError:
                return False
        else:
            try:
                return cls._load_backend(name) is not None
            except exc.PasslibSecurityError:
                return None

    @classmethod
    def set_backend(cls, name="any"):
        """load specified backend to be used for future _calc_checksum() calls

        this method replaces :meth:`_calc_checksum` with a method
        which uses the specified backend.

        :arg name:
            name of backend to load, defaults to ``"any"``.
            this can be any of the following values:

            * any string in :attr:`backends`,
              indicating the specific backend to use.

            * the special string ``"default"``, which means to use
              the preferred backend on the given host
              (this is generally the first backend in :attr:`backends`
              which can be loaded).

            * the special string ``"any"``, which means to use
              the current backend if one has been loaded,
              else acts like ``"default"``.

        :raises passlib.exc.MissingBackendError:
            * ... if a specific backend was requested,
              but is not currently available.

            * ... if ``"any"`` or ``"default"`` was specified,
              and *no* backends are currently available.

        :raises passlib.exc.PasslibSecurityError:

            if ``"any"`` or ``"default"`` was specified,
            but the only backend available has a PasslibSecurityError.
            may be raised by the loading code, or by a subclassed set_backend() function.

        :returns:
            The return value of this function should be ignored.
        """
        if name == "any" and cls._backend:
            # keep active backend
            return cls._backend
        elif name == "any" or name == "default":
            # select default backend
            failed_name = None
            for name in cls.backends:
                try:
                    calc = cls._load_backend(name)
                except exc.PasslibSecurityError:
                    # backend is available, but refuses to load due to security issue.
                    if failed_name is None:
                        failed_name = name
                    continue
                if calc:
                    break
                assert calc is None
            else:
                if failed_name:
                    # if there was at least one backend, but it had a PasslibSecurityError,
                    # report that to the user rather than MissingBackendError
                    cls._load_backend(failed_name)
                msg = "%s: no backends available" % cls.name
                if cls._no_backend_suggestion:
                    msg += cls._no_backend_suggestion
                raise exc.MissingBackendError(msg)
        else:
            # select specific backend
            calc = cls._load_backend(name)
            if not calc:
                assert calc is None
                raise exc.MissingBackendError("%s: backend not available: %s" %
                                              (cls.name, name))
        # load backend into class
        # NOTE: not overwriting _calc_checksum() directly, so that classes can provide
        #       common behavior in that method,
        #       and then invoke _calc_checksum_backend() to do the work.
        assert callable(calc)
        cls._calc_checksum_backend = calc
        cls._backend = name
        return name

    @classmethod
    def _load_backend(cls, name):
        """helper used by has_backend() & set_backend(), loads specified backend.

        :raises ValueError: if invalid backend name is provided

        :raises passlib.exc.SecurityError:
            backend code itself is allowed to raise this if backend is available,
            but a fatal security issue was found.

        :returns:
            * ``None`` if backend can't be loaded.
            * backend-specific ``_calc_checksum()`` callable on success.
        """
        # validate name
        if name not in cls.backends:
            raise ValueError("%s: unknown backend: %r" % (cls.name, name))

        # new in v1.7: check for _load_backend_xxx() function
        load = getattr(cls, "_load_backend_" + name, None)
        if load is not None:
            assert not hasattr(cls, "_has_backend_" + name), (
                "%s: can't specify both ._load_backend_%s() "
                "and ._has_backend_%s" % (cls.name, name, name)
                )
            return load()

        # fallback to _has_backend_xxx + _calc_checksum_xxx() style
        value = getattr(cls, "_has_backend_" + name)
        warn("%s: support for ._has_backend_%s is deprecated as of Passlib 1.7, "
             "and will be removed in Passlib 2.0, please implement "
             "._load_backend_%s() instead" % (cls.name, name, name),
             DeprecationWarning,
             )
        if value:
            return getattr(cls, "_calc_checksum_" + name)
        else:
            return None

    def _calc_checksum_backend(self, secret):
        """
        stub for _calc_checksum_backend(),
        the default backend will be selected the first time stub is called.
        """
        # if we got here, no backend has been loaded; so load default backend
        assert not self._backend, "set_backend() failed to replace lazy loader"
        self.set_backend()
        assert self._backend, "set_backend() failed to load a default backend"

        # this should now invoke the backend-specific version, so call it again.
        return self._calc_checksum_backend(secret)

    def _calc_checksum(self, secret):
        "wrapper for backend, for common code"""
        return self._calc_checksum_backend(secret)

#=============================================================================
# wrappers
#=============================================================================
# XXX: should this inherit from PasswordHash?
class PrefixWrapper(object):
    """wraps another handler, adding a constant prefix.

    instances of this class wrap another password hash handler,
    altering the constant prefix that's prepended to the wrapped
    handlers' hashes.

    this is used mainly by the :doc:`ldap crypt <passlib.hash.ldap_crypt>` handlers;
    such as :class:`~passlib.hash.ldap_md5_crypt` which wraps :class:`~passlib.hash.md5_crypt` and adds a ``{CRYPT}`` prefix.

    usage::

        myhandler = PrefixWrapper("myhandler", "md5_crypt", prefix="$mh$", orig_prefix="$1$")

    :param name: name to assign to handler
    :param wrapped: handler object or name of registered handler
    :param prefix: identifying prefix to prepend to all hashes
    :param orig_prefix: prefix to strip (defaults to '').
    :param lazy: if True and wrapped handler is specified by name, don't look it up until needed.
    """

    def __init__(self, name, wrapped, prefix=u(''), orig_prefix=u(''), lazy=False,
                 doc=None, ident=None):
        self.name = name
        if isinstance(prefix, bytes):
            prefix = prefix.decode("ascii")
        self.prefix = prefix
        if isinstance(orig_prefix, bytes):
            orig_prefix = orig_prefix.decode("ascii")
        self.orig_prefix = orig_prefix
        if doc:
            self.__doc__ = doc
        if hasattr(wrapped, "name"):
            self._set_wrapped(wrapped)
        else:
            self._wrapped_name = wrapped
            if not lazy:
                self._get_wrapped()

        if ident is not None:
            if ident is True:
                # signal that prefix is identifiable in itself.
                if prefix:
                    ident = prefix
                else:
                    raise ValueError("no prefix specified")
            if isinstance(ident, bytes):
                ident = ident.decode("ascii")
            # XXX: what if ident includes parts of wrapped hash's ident?
            if ident[:len(prefix)] != prefix[:len(ident)]:
                raise ValueError("ident must agree with prefix")
            self._ident = ident

    _wrapped_name = None
    _wrapped_handler = None

    def _set_wrapped(self, handler):
        # check this is a valid handler
        if 'ident' in handler.setting_kwds and self.orig_prefix:
            # TODO: look into way to fix the issues.
            warn("PrefixWrapper: 'orig_prefix' option may not work correctly "
                 "for handlers which have multiple identifiers: %r" %
                 (handler.name,), exc.PasslibRuntimeWarning)

        # store reference
        self._wrapped_handler = handler

    def _get_wrapped(self):
        handler = self._wrapped_handler
        if handler is None:
            handler = get_crypt_handler(self._wrapped_name)
            self._set_wrapped(handler)
        return handler

    wrapped = property(_get_wrapped)

    _ident = False

    @property
    def ident(self):
        value = self._ident
        if value is False:
            value = None
            # XXX: how will this interact with orig_prefix ?
            #      not exposing attrs for now if orig_prefix is set.
            if not self.orig_prefix:
                wrapped = self.wrapped
                ident = getattr(wrapped, "ident", None)
                if ident is not None:
                    value = self._wrap_hash(ident)
            self._ident = value
        return value

    _ident_values = False

    @property
    def ident_values(self):
        value = self._ident_values
        if value is False:
            value = None
            # XXX: how will this interact with orig_prefix ?
            #      not exposing attrs for now if orig_prefix is set.
            if not self.orig_prefix:
                wrapped = self.wrapped
                idents = getattr(wrapped, "ident_values", None)
                if idents:
                    value = [ self._wrap_hash(ident) for ident in idents ]
                ##else:
                ##    ident = self.ident
                ##    if ident is not None:
                ##        value = [ident]
            self._ident_values = value
        return value

    # attrs that should be proxied
    # XXX: change this to proxy everything that doesn't start with "_"?
    _proxy_attrs = (
                    "setting_kwds", "context_kwds",
                    "default_rounds", "min_rounds", "max_rounds", "rounds_cost",
                    "min_desired_rounds", "max_desired_rounds", "vary_rounds",
                    "default_salt_size", "min_salt_size", "max_salt_size",
                    "salt_chars", "default_salt_chars",
                    "backends", "has_backend", "get_backend", "set_backend",
                    )

    def __repr__(self):
        args = [ repr(self._wrapped_name or self._wrapped_handler) ]
        if self.prefix:
            args.append("prefix=%r" % self.prefix)
        if self.orig_prefix:
            args.append("orig_prefix=%r" % self.orig_prefix)
        args = ", ".join(args)
        return 'PrefixWrapper(%r, %s)' % (self.name, args)

    def __dir__(self):
        attrs = set(dir(self.__class__))
        attrs.update(self.__dict__)
        wrapped = self.wrapped
        attrs.update(
            attr for attr in self._proxy_attrs
            if hasattr(wrapped, attr)
        )
        return list(attrs)

    def __getattr__(self, attr):
        """proxy most attributes from wrapped class (e.g. rounds, salt size, etc)"""
        if attr in self._proxy_attrs:
            return getattr(self.wrapped, attr)
        raise AttributeError("missing attribute: %r" % (attr,))

    def _unwrap_hash(self, hash):
        """given hash belonging to wrapper, return orig version"""
        # NOTE: assumes hash has been validated as unicode already
        prefix = self.prefix
        if not hash.startswith(prefix):
            raise exc.InvalidHashError(self)
        # NOTE: always passing to handler as unicode, to save reconversion
        return self.orig_prefix + hash[len(prefix):]

    def _wrap_hash(self, hash):
        """given orig hash; return one belonging to wrapper"""
        # NOTE: should usually be native string.
        # (which does mean extra work under py2, but not py3)
        if isinstance(hash, bytes):
            hash = hash.decode("ascii")
        orig_prefix = self.orig_prefix
        if not hash.startswith(orig_prefix):
            raise exc.InvalidHashError(self.wrapped)
        wrapped = self.prefix + hash[len(orig_prefix):]
        return uascii_to_str(wrapped)

    def replace(self, **kwds):
        # generate subclass of wrapped handler
        subcls = self.wrapped.replace(**kwds)
        assert subcls is not self.wrapped
        # then create identical wrapper which wraps the new subclass.
        return PrefixWrapper(self.name, subcls, prefix=self.prefix, orig_prefix=self.orig_prefix)

    def needs_update(self, hash, **kwds):
        hash = self._unwrap_hash(hash)
        return self.wrapped.needs_update(hash, **kwds)

    def identify(self, hash):
        hash = to_unicode_for_identify(hash)
        if not hash.startswith(self.prefix):
            return False
        hash = self._unwrap_hash(hash)
        return self.wrapped.identify(hash)

    @deprecated_method(deprecated="1.7", removed="2.0")
    def genconfig(self, **kwds):
        config = self.wrapped.genconfig(**kwds)
        if config is None:
            raise RuntimeError(".genconfig() must return a string, not None")
        return self._wrap_hash(config)

    @deprecated_method(deprecated="1.7", removed="2.0")
    def genhash(self, secret, config, **kwds):
        # TODO: under 2.0, throw TypeError if config is None, rather than passing it through
        if config is not None:
            config = to_unicode(config, "ascii", "config/hash")
            config = self._unwrap_hash(config)
        return self._wrap_hash(self.wrapped.genhash(secret, config, **kwds))

    @deprecated_method(deprecated="1.7", removed="2.0", replacement=".hash()")
    def encrypt(self, secret, **kwds):
        return self.hash(secret, **kwds)

    def hash(self, secret, **kwds):
        return self._wrap_hash(self.wrapped.hash(secret, **kwds))

    def verify(self, secret, hash, **kwds):
        hash = to_unicode(hash, "ascii", "hash")
        hash = self._unwrap_hash(hash)
        return self.wrapped.verify(secret, hash, **kwds)

#=============================================================================
# eof
#=============================================================================
