#!/usr/bin/env python
#First run the command avahi-publish-service TestConnect _spotify-connect._tcp 4000 VERSION=1.0 CPath=/login/_zeroconf
#TODO: Add error checking
#TODO: Show when request fails on webpage
import os
import sys
import argparse
import logging
import re
import signal
import json
import uuid
from flask import Flask, request, abort, jsonify, redirect, url_for
from gevent.wsgi import WSGIServer
from gevent import spawn_later, sleep
from connect_ffi import ffi, lib, C
from utils import get_zeroconf_vars, get_metadata, get_image_url
from console_callbacks import audio_arg_parser, mixer, error_callback, connection_callbacks, debug_callbacks, playback_callbacks, playback_setup
from utils import print_zeroconf_vars

LOGGER = logging.getLogger("spotify-connect-web")
LOGGER.addHandler(logging.StreamHandler())
LOGGER.setLevel(logging.INFO)


class Connect:
    def __init__(self, error_cb = error_callback, web_arg_parser = None):
        arg_parsers = [audio_arg_parser]
        if web_arg_parser:
            arg_parsers.append(web_arg_parser)
        arg_parser = argparse.ArgumentParser(description='Web interface for Spotify Connect', parents=arg_parsers)
        arg_parser.add_argument('--debug', '-d', help='enable libspotify_embedded/flask debug output', action="store_true")
        arg_parser.add_argument('--key', '-k', help='path to spotify_appkey.key (can be obtained from https://developer.spotify.com/my-account/keys )', default='spotify_appkey.key')
        arg_parser.add_argument('--username', '-u', help='your spotify username')
        arg_parser.add_argument('--password', '-p', help='your spotify password')
        arg_parser.add_argument('--name', '-n', help='name that shows up in the spotify client', default='TestConnect')
        arg_parser.add_argument('--bitrate', '-b', help='Sets bitrate of audio stream (may not actually work)', choices=[90, 160, 320], type=int, default=160)
        arg_parser.add_argument('--credentials', '-c', help='File to load and save credentials from/to', default='credentials.json')
        self.args = arg_parser.parse_args()
        try:
            with open(self.args.key) as f:
                app_key = ffi.new('uint8_t *')
                f.readinto(ffi.buffer(app_key))
                app_key_size = len(f.read()) + 1
        except IOError as e:
            print "Error opening app key: {}.".format(e)
            print "If you don't have one, it can be obtained from https://developer.spotify.com/my-account/keys"
            sys.exit(1)
        self.credentials = dict({
            'device-id': str(uuid.uuid4()),
            'username': None,
            'blob': None
        })
        try:
            with open(self.args.credentials) as f:
                self.credentials.update(
                        { k: v.encode('utf-8') if isinstance(v, unicode) else v
                            for (k,v)
                            in json.loads(f.read()).iteritems() })
        except IOError:
            pass
        if self.args.username:
            self.credentials['username'] = self.args.username
        userdata = ffi.new_handle(self)
        if self.args.debug:
            lib.SpRegisterDebugCallbacks(debug_callbacks, userdata)
        self.config = {
             'version': 4,
             'buffer': C.malloc(0x100000),
             'buffer_size': 0x100000,
             'app_key': app_key,
             'app_key_size': app_key_size,
             'deviceId': ffi.new('char[]', self.credentials['device-id']),
             'remoteName': ffi.new('char[]', self.args.name),
             'brandName': ffi.new('char[]', 'DummyBrand'),
             'modelName': ffi.new('char[]', 'DummyModel'),
             'client_id': ffi.new('char[]', '0'),
             'deviceType': lib.kSpDeviceTypeAudioDongle,
             'error_callback': error_cb,
             'userdata': userdata,
        }

        init = ffi.new('SpConfig *' , self.config)
        init_status = lib.SpInit(init)
        print "SpInit: {}".format(init_status)
        if init_status != 0:
            print "SpInit failed, exiting"
            sys.exit(1)
        lib.SpRegisterConnectionCallbacks(connection_callbacks, userdata)
        lib.SpRegisterPlaybackCallbacks(playback_callbacks, userdata)

        mixer_volume = int(mixer.getvolume()[0] * 655.35)
        lib.SpPlaybackUpdateVolume(mixer_volume)
        bitrates = {
            90: lib.kSpBitrate90k,
            160: lib.kSpBitrate160k,
            320: lib.kSpBitrate320k
        }
        lib.SpPlaybackSetBitrate(bitrates[self.args.bitrate])
        playback_setup()
        print_zeroconf_vars()

        if self.credentials['username'] and self.args.password:
            self.login(password=self.args.password)
        elif self.credentials['username'] and self.credentials['blob']:
            self.login(blob=self.credentials['blob'])
        else:
            if __name__ == '__main__':
                raise ValueError("No username given, and none stored")

    def login(self, username=None, password=None, blob=None, zeroconf=None):
        if username is not None:
            self.credentials['username'] = username
        elif self.credentials['username']:
            username = self.credentials['username']
        else:
            raise ValueError("No username given, and none stored")

        if password is not None:
            lib.SpConnectionLoginPassword(username, password)
        elif blob is not None:
            lib.SpConnectionLoginBlob(username, blob)
        elif zeroconf is not None:
            lib.SpConnectionLoginZeroConf(username, *zeroconf)
        else:
            raise ValueError("Must specify a login method (password, blob or zeroconf)")


web_arg_parser = argparse.ArgumentParser(add_help=False)
args = web_arg_parser.parse_known_args()[0]

app = Flask(__name__, root_path=sys.path[0])
app.config.from_object(__name__)
app.config['SECRET_KEY'] = '7d441f27d441f27567d441f2b6176b'

#Used by the error callback to determine login status
invalid_login = False

def signal_handler(signal, frame):
    lib.SpConnectionLogout()
    lib.SpFree()
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@ffi.callback('void(SpError error, void *userdata)')
def web_error_callback(error, userdata):
    global invalid_login
    if error == lib.kSpErrorLoginBadCredentials:
        invalid_login = True

connect_app = Connect(web_error_callback, web_arg_parser)


##Routes


#Playback routes
@app.route('/api/playback/play')
def playback_play():
    lib.SpPlaybackPlay()
    return '', 204

@app.route('/api/playback/pause')
def playback_pause():
    lib.SpPlaybackPause()
    return '', 204

@app.route('/api/playback/prev')
def playback_prev():
    lib.SpPlaybackSkipToPrev()
    return '', 204

@app.route('/api/playback/next')
def playback_next():
    lib.SpPlaybackSkipToNext()
    return '', 204

#TODO: Add ability to disable shuffle/repeat
@app.route('/api/playback/shuffle')
def playback_shuffle():
    lib.SpPlaybackEnableShuffle(True)
    return '', 204

@app.route('/api/playback/shuffle/<status>', endpoint='shuffle_toggle')
def playback_shuffle(status):
    if status == 'enable':
        lib.SpPlaybackEnableShuffle(True)
    elif status == 'disable':
        lib.SpPlaybackEnableShuffle(False)
    return '', 204

@app.route('/api/playback/repeat')
def playback_repeat():
    lib.SpPlaybackEnableRepeat(True)
    return '', 204

@app.route('/api/playback/repeat/<status>', endpoint='repeat_toggle')
def playback_repeat(status):
    if status == 'enable':
        lib.SpPlaybackEnableRepeat(True)
    elif status == 'disable':
        lib.SpPlaybackEnableRepeat(False)
    return '', 204


@app.route('/api/playback/volume', methods=['GET'])
def playback_volume():
    return jsonify({
        'volume': lib.SpPlaybackGetVolume()
    })

@app.route('/api/playback/volume', methods=['POST'], endpoint='playback_volume-post')
def playback_volume():
    volume = request.form.get('value')
    if volume is None:
        return jsonify({
            'error': 'value must be set'
        }), 400
    lib.SpPlaybackUpdateVolume(int(volume))
    return '', 204


#Info routes
@app.route('/api/info/metadata')
def info_metadata():
    res = get_metadata()
    res['volume'] = lib.SpPlaybackGetVolume()
    return jsonify(res)

@app.route('/api/info/status')
def info_status():
    return jsonify({
        'active': bool(lib.SpPlaybackIsActiveDevice()),
        'playing': bool(lib.SpPlaybackIsPlaying()),
        'shuffle': bool(lib.SpPlaybackIsShuffled()),
        'repeat': bool(lib.SpPlaybackIsRepeated()),
        'logged_in': bool(lib.SpConnectionIsLoggedIn())
    })

@app.route('/api/info/image_url/<image_uri>')
def info_image_url(image_uri):
    return redirect(get_image_url(str(image_uri)))

@app.route('/api/info/display_name', methods=['GET'])
def info_display_name():
    return jsonify({
        'remoteName': get_zeroconf_vars()['remoteName']
    })

@app.route('/api/info/display_name', methods=['POST'], endpoint='display_name-post')
def info_display_name():
    display_name = str(request.form.get('displayName'))
    if not display_name:
        return jsonify({
            'error': 'displayName must be set'
        }), 400
    lib.SpSetDisplayName(display_name)
    return '', 204

#Login routes
@app.route('/login/logout')
def login_logout():
    lib.SpConnectionLogout()

@app.route('/login/password', methods=['POST'])
def login_password():
    global invalid_login
    invalid_login = False
    username = str(request.form.get('username'))
    password = str(request.form.get('password'))
    if username and password:
        connect_app.login(username, password=password)

@app.route('/login/check_login')
def check_login():
    res = {
        'finished': False,
        'success': False
    }

    if invalid_login:
        res['finished'] = True
    elif bool(lib.SpConnectionIsLoggedIn()):
        res['finished'] = True
        res['success'] = True

    return jsonify(res)

@app.route('/login/_zeroconf', methods=['GET', 'POST'])
def login_zeroconf():
    action = request.args.get('action') or request.form.get('action')
    if not action:
        return jsonify({
            'status': 301,
            'spotifyError': 0,
            'statusString': 'ERROR-MISSING-ACTION'})
    if action == 'getInfo' and request.method == 'GET':
        return get_info()
    elif action == 'addUser' and request.method == 'POST':
        return add_user()
    else:
        return jsonify({
            'status': 301,
            'spotifyError': 0,
            'statusString': 'ERROR-INVALID-ACTION'})

def get_info():
    zeroconf_vars = get_zeroconf_vars()

    return jsonify({
        'status': 101,
        'spotifyError': 0,
        'activeUser': zeroconf_vars['activeUser'],
        'brandDisplayName': ffi.string(connect_app.config['brandName']),
        'accountReq': zeroconf_vars['accountReq'],
        #Doesn't have any specific format (I think)
        'deviceID': zeroconf_vars['deviceId'],
        #Generated from SpZeroConfGetVars()
        #Used to encrypt the blob used for login
        'publicKey': zeroconf_vars['publicKey'],
        'version': '2.0.1',
        #Valid types are UNKNOWN, COMPUTER, TABLET, SMARTPHONE, SPEAKER, TV, AVR, STB and AUDIODONGLE
        'deviceType': zeroconf_vars['deviceType'],
        'modelDisplayName': ffi.string(connect_app.config['modelName']),
        #Status codes are ERROR-OK (not actually an error), ERROR-MISSING-ACTION, ERROR-INVALID-ACTION, ERROR-SPOTIFY-ERROR, ERROR-INVALID-ARGUMENTS, ERROR-UNKNOWN, and ERROR_LOG_FILE
        'statusString': 'ERROR-OK',
        #Name that shows up in the Spotify client
        'remoteName': zeroconf_vars['remoteName']
    })

def add_user():
    args = request.form
    #TODO: Add parameter verification
    username = str(args.get('userName'))
    blob = str(args.get('blob'))
    clientKey = str(args.get('clientKey'))
    connect_app.login(username, zeroconf=(blob,clientKey))
    return jsonify({
        'status': 101,
        'spotifyError': 0,
        'statusString': 'ERROR-OK'
        })

spawn_later(0.1, lib.SpPumpEvents)
if __name__ == "__main__":
    #Loop to pump events
    http_server = WSGIServer(('', 4000), app, log=None)
    http_server.serve_forever()
