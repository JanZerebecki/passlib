"""passlib.handlers.roundup - Roundup issue tracker hashes"""
#=========================================================
#imports
#=========================================================
#core
import logging; log = logging.getLogger(__name__)
#site
#libs
from passlib.utils import handlers as uh
#pkg
#local
__all__ = [
    "roundup_plaintext",
    "roundup_pbkdf2_sha1",
    "ldap_hex_md5",
    "ldap_hex_sha1",
]
#=========================================================
#
#=========================================================
roundup_plaintext = uh.PrefixWrapper("roundup_plaintext", "plaintext",
                                     prefix="{plaintext}", lazy=True)

roundup_pbkdf2_sha1 = uh.PrefixWrapper("roundup_pbkdf2_sha1", "pbkdf2_sha1",
                                       prefix="{PBKDF2}",
                                       orig_prefix="$pbkdf2-sha1$", lazy=True)

#NOTE: these are here because they're currently only known to be used by roundup
ldap_hex_md5 = uh.PrefixWrapper("ldap_hex_md5", "hex_md5", "{MD5}", lazy=True)
ldap_hex_sha1 = uh.PrefixWrapper("ldap_hex_sha1", "hex_sha1", "{SHA}", lazy=True)

#=========================================================
#eof
#=========================================================
