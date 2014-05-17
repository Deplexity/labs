#!/usr/bin/env python
"""Test sqrl client.

Usage:
  client.py create --identity=<file> [--password=<pw>] [--iterations=<iterations>]
  client.py verify --identity=<file> [--password=<pw>]
  client.py update --identity=<file> [--password=<pw>] --unlock
  client.py update --identity=<file> [--password=<pw>] [--change-password=<newpw>] [--change-iterations=<iterations>]
  client.py login --identity=<file> [--password=<pw>] --url=<url>

Options:
  -h, --help                        Show this screen.
  -V, --version                     Show version.
  --identity=<file>                 Output file.
  --password=<pw>                   [default: ]
  --iterations=<iterations>         Password strength. [default: 1200]
  --change-iterations=<iterations>  Change password strength.
  --change-password=<newpw>         Change password. [default: ]
  --unlock                          Generate identity unlock keys.
  --url=<url>                       SQRL url.

"""
import os
import sys
from pysodium import *
import urlparse
import binascii
from PBKDF import PBKDF2
import test_fileformat as ff
from test_fileformat import SqrlIdentity
import requests
import base64url
from collections import namedtuple

SALT_LEN = 8L
IDK_LEN = 32L
PW_VERIFIER_LEN = 16L

class TIF(object):
    # Found idk match
    IdMatch = 0x1
    # Found pidk match
    PreviousIdMatch = 0x2
    # Client IP matches encrypted IP
    IpMatch = 0x4
    # User account is enabled
    SqrlEnabled = 0x8
    # User already logged in
    LoggedIn = 0x10
    # User can register
    AccountCreationEnabled = 0x20
    # Error processing command
    CommandFailed = 0x40
    # Server error
    SqrlFailure = 0x80

SqrlUser = namedtuple('SqrlUser', 'identity pw')
SqrlAccount = namedtuple('SqrlAccount', 'user url suk vuk tif')
SqrlResponse = namedtuple('SqrlResponse', 'suk vuk tif')

class SqrlRequest(object):
    def __init__(self, user, url):
        self.user = user
        self.url = url

    def get_command(self):
        return ''

    def get_client_args(self):
        return {}

    def response(self):
        info = urlparse.urlparse(self.url)

        if info.scheme not in ['qrl', 'sqrl']:
            raise Exception('Url scheme not supported.')

        host = info.netloc
        headers = {
            'User-Agent': 'SQRL/1'
        }

        if '@' in host:
            userpass, host = host.split('@')
            headers['Authentication'] = userpass

        pw_hash = create_pw_hash(
            self.user.pw,
            self.user.identity.salt,
            self.user.identity.pw_iterations)

        original_masterkey = xor_masterkey(
            self.user.identity.masterkey,
            pw_hash,
            self.user.identity.salt)

        pk, sk = generate_site_keypair(original_masterkey, host)

        clientargs = dict(
            ver=1,
            cmd = self.get_command(),
            idk=base64url.encode(pk)
        )

        clientargs.update(self.get_client_args())

        clientval = base64url.encode('&'.join(
            '%s=%s' % (k, v) for k, v in clientargs.iteritems()))
        serverval = base64url.encode(self.url)
        m = clientval + serverval
        ids = base64url.encode(crypto_sign(m, sk))

        args = dict(
            client=clientval,
            server=serverval,
            ids=ids
        )

        payload = '&'.join('%s=%s' % (k, v) for k, v in args.iteritems())

        if info.scheme == 'sqrl':
            post_url = url.replace('sqrl://', 'https://')
        else:
            post_url = url.replace('qrl://', 'http://')

        r = requests.post(post_url, data=payload, headers=headers)

        print r.text

        return self.parse_response(r)

    def parse_response(self, res):
        suk = ""
        vuk = ""
        tif = 0
        return SqrlResponse(suk, vuk, tif)


def create_identity(pw, iterations):
    """Return randomly generated identity encrypted with pw.
    This does not include creation of identity unlock keys.
    """
    iterations = max(iterations, 1)
    salt = crypto_stream(SALT_LEN)
    pw_hash = create_pw_hash(pw, salt, iterations)
    pw_verify = create_pw_verify(pw_hash)
    masterkey = xor_masterkey(crypto_stream(IDK_LEN), pw_hash, salt)
    identity = SqrlIdentity(
        masterkey=masterkey,
        salt=salt,
        pw_iterations=iterations,
        pw_verify=pw_verify,
        identity_lock_key=''
    )
    return identity

def verify_password(identity, pw):
    """Return true if pw hash matches identity pw verification hash."""
    pw_hash = create_pw_hash(pw, identity.salt, identity.pw_iterations)
    pw_verify = create_pw_verify(pw_hash)
    return pw_verify == identity.pw_verify

def change_pw(identity, pw, newpw, newiterations):
    newiterations = max(newiterations, 1)

    # Confirm valid password.
    pw_hash = create_pw_hash(pw, identity.salt, identity.pw_iterations)
    pw_verify = create_pw_verify(pw_hash)
    if pw_verify != identity.pw_verify:
        raise Exception('Invalid password.')

    # Decrypt key with old pw.
    original_masterkey = xor_masterkey(identity.masterkey, pw_hash, identity.salt)

    # Encrypt with new pw.
    salt = crypto_stream(SALT_LEN)
    pw_hash = create_pw_hash(newpw, salt, newiterations)
    pw_verify = create_pw_verify(pw_hash)
    masterkey = xor_masterkey(original_masterkey, pw_hash, salt)
    identity.salt = salt
    identity.pw_iterations = newiterations
    identity.pw_verify = pw_verify
    identity.masterkey = masterkey

def save_identity(file, identity):
    with open(file, 'wb') as fs:
        ff.save(fs, identity)

def load_identity(file):
    with open(file, 'rb') as fs:
        return ff.load(fs)

def xor_masterkey(masterkey, pw_hash, salt):
    """Return masterkey XOR pw_hash with salt."""
    return crypto_stream_xor(masterkey, len(masterkey), key=pw_hash, nonce=salt)

def create_pw_hash(pw, salt, iterations):
    return PBKDF2(pw, salt, c=iterations)

def create_pw_verify(pw_hash):
    return crypto_generichash(pw_hash, outlen=PW_VERIFIER_LEN)

def generate_site_keypair(masterkey, domain):
    """Return keypair based on site and master key"""
    seed = crypto_generichash(domain, k=masterkey)
    pk, sk = crypto_sign_seed_keypair(seed)
    return pk, sk

def fetch_account(user, url):
    req = SqrlRequest(user, url)
    res = req.response()
    suk = res.suk
    vuk = res.vuk
    tif = res.tif
    return SqrlAccount(user, url, suk, vuk, tif)

def login(account):
    print account
    #info = probe_server(url)
    #cmd = SqrlLoginRequest(account)
    #print cmd.response()
    # based on TIF... login

if __name__ == '__main__':
    from docopt import docopt
    args = docopt(__doc__, version='1.0.0')

    # Disable output buffering.
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    #print(args)
    #sys.exit(0)

    if args['login']:
        url = args['--url']
        password = args['--password']
        identity = load_identity(args['--identity'])
        user = SqrlUser(identity, password)
        account = fetch_account(user, url)
        login(account)
        sys.exit(0)
    elif args['register']:
        sys.exit(0)
    elif args['create']:
        password = args['--password']
        iterations = int(args['--iterations'])
        identity = create_identity(password, iterations)
        save_identity(args['--identity'], identity)
        sys.exit(0)
    elif args['update']:
        identity = load_identity(args['--identity'])
        if args['--unlock']:
            # Confirm password is valid before making any changes.
            if not verify_password(identity, args['--password']):
                print('Invalid password.')
                sys.exit(1)
            ilk, iuk = crypto_box_keypair()
            identity.identity_lock_key = ilk
            save_identity(args['--identity'], identity)
            print('Here is your identity unlock key. You will need this to make changes to your account later.')
            print('Store this in a safe location!')
            print(binascii.hexlify(iuk))
            sys.exit(0)
        else:
            # We don't need to confirm the password before changing it because the change password
            # function already does that.
            password = args['--password']
            newpassword = args['--change-password']
            newiterations = identity.pw_iterations
            if args['--change-iterations']:
                newiterations = int(args['--change-iterations'])
            change_pw(identity, password, newpassword, newiterations)
            save_identity(args['--identity'], identity)
            sys.exit(0)
    elif args['verify']:
        identity = load_identity(args['--identity'])
        if verify_password(identity, args['--password']):
            sys.exit(0)
        else:
            print('Invalid password.')
            sys.exit(1)
