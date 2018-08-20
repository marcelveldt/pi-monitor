#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import subprocess
from resources.lib.utils import PlayerMetaData, check_software, PLAYING_STATE, IDLE_STATE, NOTIFY_STATE, ALERT_STATE

'''
    LocalPlayer
    very basic local media player using sox, allowing to play local/remote files (such as notifications)
'''

def setup(monitor):
    '''setup the module'''
    if not check_software(bin_path="/usr/bin/play", installapt="sox libsox-fmt-all"):
        LOGGER.error("sox is not installed! Please install manually")
        return False
    return LocalPlayer(monitor)



class LocalPlayer(object):
    _exit = threading.Event()
    _sox_proc = None
    _playing = False

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["localplayer"] = PlayerMetaData("LocalPlayer")
        
    def stop(self):
        self._exit.set()
        self._stop_playing()

    def start(self):
        pass

    def command(self, cmd, *args, **kwargs):
        ''' send command to roon output/zone'''
        loop = kwargs.get("loop")
        url = kwargs.get("url")
        if not url:
            url = args[0] if args else ""
        if cmd in ["stop", "pause"]:
            self._stop_playing()
            return True
        elif cmd == "play_media":
            self.play_media(url, loop, PLAYING_STATE)
            return True
        elif cmd == "play_notification":
            self.play_media(url, loop, NOTIFY_STATE)
            return True
        elif cmd == "play_alert":
            self.play_media(url, loop, ALERT_STATE)
            return True
        else:
            return False

    def play_media(self, url, loop=False, playback_state=PLAYING_STATE):
        ''' play media file with local sox player '''
        LOGGER.debug("play_media: %s - loop: %s" %(url, loop))
        self._stop_playing()
        self._playing = True
        self.monitor.states["localplayer"]["state"] = playback_state
        self.monitor.states["localplayer"]["title"] = url # todo: extract metadata from playing file?
        args = ["/usr/bin/play", url]
        while self._playing and not self._exit.isSet():
            self._sox_proc = subprocess.Popen(args)
            self._sox_proc.wait()
            if not loop:
                break
        self._playing = False
        self.monitor.states["localplayer"]["state"] = IDLE_STATE

    def _stop_playing(self):
        ''' make sure that any playing sox player stops playing '''
        self._playing = False
        if self._sox_proc:
            self._sox_proc.terminate()
        