from __future__ import division
import argparse
import alsaaudio as alsa
import json
import Queue
from threading import Thread
import threading
from connect_ffi import ffi, lib
import logging
import urllib2
from utils import LOGGER
import sys

RATE = 44100
CHANNELS = 2
PERIODSIZE = int(44100 / 4) # 0.25s
SAMPLESIZE = 2 # 16 bit integer
MAXPERIODS = int(0.5 * RATE / PERIODSIZE) # 0.5s Buffer

audio_arg_parser = argparse.ArgumentParser(add_help=False)

playback_device_group = audio_arg_parser.add_mutually_exclusive_group()
playback_device_group.add_argument('--device', '-D', help='alsa output device (deprecated, use --playback_device)', default='default')
playback_device_group.add_argument('--playback_device', '-o', help='alsa output device (get name from aplay -L)', default='default')

audio_arg_parser.add_argument('--mixer_device_index', help='alsa card index of the mixer device', type=int)
audio_arg_parser.add_argument('--mixer', '-m', help='alsa mixer name for volume control', default='')
audio_arg_parser.add_argument('--dbrange', '-r', help='alsa mixer volume range in Db', default=0)
args = audio_arg_parser.parse_known_args()[0]

global is_exited
is_exited = False


class PlaybackSession:

    def __init__(self):
        self._active = False

    def is_active(self):
        return self._active

    def activate(self):
        self._active = True

    def deactivate(self):
        self._active = False

class AlsaSink:

    def __init__(self, session, args):
        self._lock = threading.Lock()
        self._args = args
        self._session = session
        self._device = None

    def acquire(self):
        if self._session.is_active():
            try:
                pcm_args = {
                    'type': alsa.PCM_PLAYBACK,
                    'mode': alsa.PCM_NORMAL,
                }
                if self._args.playback_device != 'default':
                    pcm_args['device'] = self._args.playback_device
                else:
                    pcm_args['card'] = self._args.device
                pcm = alsa.PCM(**pcm_args)

                pcm.setchannels(CHANNELS)
                pcm.setrate(RATE)
                pcm.setperiodsize(PERIODSIZE)
                pcm.setformat(alsa.PCM_FORMAT_S16_LE)

                self._device = pcm
                LOGGER.info("AlsaSink: device acquired")
            except alsa.ALSAAudioError as error:
                LOGGER.error("Unable to acquire device: %s" % error)
                self.release()


    def release(self):
        if self._session.is_active() and self._device is not None:
            self._lock.acquire()
            try:
                if self._device is not None:
                    self._device.close()
                    self._device = None
                    LOGGER.info("AlsaSink: device released")
            finally:
                self._lock.release()

    def write(self, data):
        if self._session.is_active() and self._device is not None:
            # write is asynchronous, so, we are in race with releasing the device
            self._lock.acquire()
            try:
                if self._device is not None:
                    self._device.write(data)
            except alsa.ALSAAudioError as error:
                LOGGER.error("Ups! Some badness happened: %s" % error)
            finally:
                self._lock.release()

session = PlaybackSession()
device = AlsaSink(session, args)
LOGGER.info("mixer: %s" % args.mixer)
mixer = alsa.Mixer(args.mixer)

try:
    mixer.getmute()
    mute_available = True
except alsa.ALSAAudioError:
    mute_available = False
    LOGGER.info( "Device has no native mute")

#Gets mimimum volume Db for the mixer
volume_range = (mixer.getrange()[1]-mixer.getrange()[0]) / 100
selected_volume_range = int(args.dbrange)
if selected_volume_range > volume_range or selected_volume_range == 0:
    selected_volume_range = volume_range
min_volume_range = (1 - selected_volume_range / volume_range) * 100

def userdata_wrapper(f):
    def inner(*args):
        assert len(args) > 0
        self = ffi.from_handle(args[-1])
        return f(self, *args[:-1])
    return inner

#Error callbacks
@ffi.callback('void(SpError error, void *userdata)')
def error_callback(error, userdata):
    LOGGER.error("error_callback: {}".format(error))


def report_state(msg):
    ''' report state update to pi-monitor to prevent polling'''
    LOGGER.debug(msg)
    if is_exited:
        return
    try:
        urllib2.urlopen("http://localhost/command?target=spotify&command=update&data=%s" % msg, timeout=0.1)
    except SystemExit, KeyboardInterrupt:
        pass
    except Exception as exc:
        LOGGER.error(exc)


#Connection callbacks
@ffi.callback('void(SpConnectionNotify type, void *userdata)')
@userdata_wrapper
def connection_notify(self, type):
    if type == lib.kSpConnectionNotifyLoggedIn:
        report_state("kSpConnectionNotifyLoggedIn")
    elif type == lib.kSpConnectionNotifyLoggedOut:
        report_state("kSpConnectionNotifyLoggedOut")
    elif type == lib.kSpConnectionNotifyTemporaryError:
        LOGGER.error("kSpConnectionNotifyTemporaryError")
    else:
        LOGGER.warning("UNKNOWN ConnectionNotify {}".format(type))

@ffi.callback('void(const char *blob, void *userdata)')
@userdata_wrapper
def connection_new_credentials(self, blob):
    LOGGER.debug(ffi.string(blob))
    self.credentials['blob'] = ffi.string(blob)
    with open(self.args.credentials, 'w') as f:
        f.write(json.dumps(self.credentials))

#Debug callbacks
@ffi.callback('void(const char *msg, void *userdata)')
@userdata_wrapper
def debug_message(self, msg):
    LOGGER.debug(ffi.string(msg))

#Playback callbacks
@ffi.callback('void(SpPlaybackNotify type, void *userdata)')
@userdata_wrapper
def playback_notify(self, type):
    if type == lib.kSpPlaybackNotifyPlay:
        device.acquire()
        report_state("kSpPlaybackNotifyPlay")
    elif type == lib.kSpPlaybackNotifyPause:
        device.release()
        report_state("kSpPlaybackNotifyPause")
    elif type == lib.kSpPlaybackNotifyTrackChanged:
        report_state("kSpPlaybackNotifyTrackChanged")
    elif type == lib.kSpPlaybackNotifyNext:
        LOGGER.debug("kSpPlaybackNotifyNext")
    elif type == lib.kSpPlaybackNotifyPrev:
        LOGGER.debug("kSpPlaybackNotifyPrev")
    elif type == lib.kSpPlaybackNotifyShuffleEnabled:
        LOGGER.debug("kSpPlaybackNotifyShuffleEnabled")
    elif type == lib.kSpPlaybackNotifyShuffleDisabled:
        LOGGER.debug("kSpPlaybackNotifyShuffleDisabled")
    elif type == lib.kSpPlaybackNotifyRepeatEnabled:
        LOGGER.debug("kSpPlaybackNotifyRepeatEnabled")
    elif type == lib.kSpPlaybackNotifyRepeatDisabled:
        LOGGER.debug("kSpPlaybackNotifyRepeatDisabled")
    elif type == lib.kSpPlaybackNotifyBecameActive:
        session.activate()
        report_state("kSpPlaybackNotifyBecameActive")
    elif type == lib.kSpPlaybackNotifyBecameInactive:
        device.release()
        session.deactivate()
        report_state("kSpPlaybackNotifyBecameInactive")
    elif type == lib.kSpPlaybackNotifyPlayTokenLost:
        LOGGER.info("kSpPlaybackNotifyPlayTokenLost")
    elif type == lib.kSpPlaybackEventAudioFlush:
        report_state("kSpPlaybackEventAudioFlush")
        #audio_flush();
    else:
        print "UNKNOWN PlaybackNotify {}".format(type)

def playback_thread(q):
    while True:
        data = q.get()
        device.write(data)
        q.task_done()

audio_queue = Queue.Queue(maxsize=MAXPERIODS)
pending_data = str()

def playback_setup():
    t = Thread(args=(audio_queue,), target=playback_thread)
    t.daemon = True
    t.start()

@ffi.callback('uint32_t(const void *data, uint32_t num_samples, SpSampleFormat *format, uint32_t *pending, void *userdata)')
@userdata_wrapper
def playback_data(self, data, num_samples, format, pending):
    global pending_data
    # Make sure we don't pass incomplete frames to alsa
    num_samples -= num_samples % CHANNELS
    buf = pending_data + ffi.buffer(data, num_samples * SAMPLESIZE)[:]
    try:
        total = 0
        while len(buf) >= PERIODSIZE * CHANNELS * SAMPLESIZE:
            audio_queue.put(buf[:PERIODSIZE * CHANNELS * SAMPLESIZE], block=False)
            buf = buf[PERIODSIZE * CHANNELS * SAMPLESIZE:]
            total += PERIODSIZE * CHANNELS

        pending_data = buf
        return num_samples
    except Queue.Full:
        return total
    finally:
        pending[0] = audio_queue.qsize() * PERIODSIZE * CHANNELS

@ffi.callback('void(uint32_t millis, void *userdata)')
@userdata_wrapper
def playback_seek(self, millis):
    LOGGER.debug("playback_seek: {}".format(millis))

@ffi.callback('void(uint16_t volume, void *userdata)')
@userdata_wrapper
def playback_volume(self, volume):
    print "playback_volume: {}".format(volume)
    if volume == 0:
        if mute_available:
            mixer.setmute(1)
            LOGGER.debug("Mute activated")
    else:
        if mute_available and mixer.getmute()[0] ==  1:
            mixer.setmute(0)
            LOGGER.debug("Mute deactivated")
        corrected_playback_volume = int(min_volume_range + ((volume / 655.35) * (100 - min_volume_range) / 100))
        LOGGER.debug("corrected_playback_volume: %s" % corrected_playback_volume)
        mixer.setvolume(corrected_playback_volume)
    report_state("playback_volume")

connection_callbacks = ffi.new('SpConnectionCallbacks *', [
    connection_notify,
    connection_new_credentials
])

debug_callbacks = ffi.new('SpDebugCallbacks *', [
    debug_message
])

playback_callbacks = ffi.new('SpPlaybackCallbacks *', [
    playback_notify,
    playback_data,
    playback_seek,
    playback_volume
])
