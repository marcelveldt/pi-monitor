#!/usr/bin/python
# -*- coding: utf-8 -*-

import requests
import thread
import socket
import threading
from resources.lib.utils import PlayerMetaData, json, requests, HOSTNAME, check_software, run_proc, subprocess, DEVNULL, STOPPED_STATE, PLAYING_STATE, PAUSED_STATE
import re
import time

def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_SQUEEZELITE", False):
        LOGGER.debug("Squeezelite module is not enabled!")
        return False
    if not check_software(dietpi_id="36", bin_path="/usr/bin/squeezelite", installapt="squeezelite"):
        LOGGER.warning("Squeezelite is not installed, please install manually.")
        return False
    import uuid
    player_mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0,8*6,8)][::-1])

    return SqueezelitePlayer(monitor, player_mac)


TAGS_FULL = "aAcCdegGijJKlostuxyRwk"  # full track/album details
TAGS_BASIC = "acdgjKluNxy"  # basic track details for initial listings
TAGS_ALBUM = "yjtiqwaal"
LOOP_WAIT = 2

class SqueezelitePlayer(threading.Thread):
    ''' LMS Class containing our helper methods'''
    _host = None
    _port = None
    _playerid = None
    _state_changing = False
    _squeezelite_proc = None
    _exit = threading.Event()
    _last_state = None

    def __init__(self, monitor, player_mac):
        self.monitor = monitor
        self._playerid = player_mac
        self.monitor.states["squeezelite"] = PlayerMetaData("Squeezelite (LMS)")
        threading.Thread.__init__(self)

    def stop(self):
        self._exit.set()
        if self._squeezelite_proc:
            self._squeezelite_proc.terminate()
            run_proc("service squeezelite start")
        threading.Thread.join(self, 10)

    def command(self, cmd, cmd_data=None):
        ''' send command to lms'''
        if cmd == "volume_up":
            return self._api_execute(
                    "change_volume_relative", {"volume":2, "output": self.output_id})
        elif cmd == "volume_down":
            return self._api_execute(
                    "change_volume_relative", {"volume":-2, "output": self.output_id})
        elif cmd == "volume_set":
            return self._set_volume(cmd_data)
        elif cmd == "next":
            self.send_request("playlist jump +1")
        elif cmd == "previous":
            self.send_request("playlist jump -1")
        elif cmd == "stop":
            self.send_request("stop")
        elif cmd == "play":
            self.send_request("play")
        elif cmd == "pause":
            self.send_request("pause 1")
        else:
            params = {
                "zone": self.output_id,
                "control": cmd
            }
            return self._api_execute("control", params=params)

    def run(self):
        # we start squuezelite manually with our optimal settings
        run_proc("service squeezelite stop", check_result=True, ignore_error=True)
        exec_path = "/usr/bin/squeezelite"
        args = [exec_path, "-C", "1", "-n", HOSTNAME, "-a", "4096:1024", "-m", self._playerid]
        if self.monitor.config["ALSA_VOLUME_CONTROL"]:
            args += ["-O", self.monitor.config["ALSA_VOLUME_CONTROL"], "-X"]
        if self.monitor.config["ALSA_SOUND_DEVICE"]:
            args += ["-o", self.monitor.config["ALSA_SOUND_DEVICE"]]
        self._squeezelite_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)
        # auto discover LMS server....
        lmsserver = None
        while not lmsserver and not self._exit.isSet():
            LOGGER.info("disovering LMS server ...")
            servers = LMSDiscovery().all()
            if servers:
                lmsserver = servers[0]  # for now, just use the first server discovered
                break
            else:
                self._exit.wait(2)
        if lmsserver:
            self._host = lmsserver["host"]
            self._port = lmsserver["port"]
            LOGGER.info("LMS server discovered - host: %s - port: %s" % (self._host, self._port))
        else:
            return
        # main loop
        while not self._exit.isSet():
            cur_state = self._get_state()
            if cur_state != self._last_state:
                self._last_state = cur_state
                self.monitor.states["squeezelite"]["state"] = cur_state
                self._update_metadata()
            if cur_state == "playing":
                self._update_metadata()
                LOOP_WAIT = 0.5
            else:
                LOOP_WAIT = 2
            self._exit.wait(LOOP_WAIT)

    def _get_state(self):
        '''set the current status of the player'''
        result = ""
        status = self.send_request("mode ?")
        if status and "_mode" in status:
            if status["_mode"] == "stop":
                result = STOPPED_STATE
            elif status["_mode"] == "play":
                result = PLAYING_STATE
            elif status["_mode"] == "pause":
                result = PAUSED_STATE
            else:
                result = status["_mode"]
        return result

    def _update_metadata(self):
        status = self.send_request("status - 1 tags:%s" % TAGS_BASIC)
        if status and not "error" in status:
            self.monitor.states["squeezelite"]["volume_level"] = status["mixer volume"]
            self.monitor.states["squeezelite"]["repeat"] = status["playlist repeat"] != 0
            self.monitor.states["squeezelite"]["shuffle"] = status["playlist shuffle"] != 0
            if status.get("playlist_loop"):
                track_details = status["playlist_loop"][0]
                self.monitor.states["squeezelite"]["artist"] = track_details["artist"]
                self.monitor.states["squeezelite"]["album"] = track_details["album"]
                self.monitor.states["squeezelite"]["title"] = track_details["title"]
                self.monitor.states["squeezelite"]["duration"] = track_details["duration"]/1000
                self.monitor.states["squeezelite"]["cover_url"] = self._get_thumb(track_details)
            else:
                self.monitor.states["squeezelite"]["artist"] = ""
                self.monitor.states["squeezelite"]["album"] = ""
                self.monitor.states["squeezelite"]["title"] = ""
                self.monitor.states["squeezelite"]["duration"] = ""
                self.monitor.states["squeezelite"]["cover_url"] = ""

    def send_request(self, cmd):
        '''send request to lms server'''
        if isinstance(cmd, (str, unicode)):
            if "[SP]" in cmd:
                new_cmd = []
                for item in cmd.split():
                    new_cmd.append(item.replace("[SP]", " "))
                cmd = new_cmd
            else:
                cmd = cmd.split()
        url = "http://%s:%s/jsonrpc.js" % (self._host, self._port)
        cmd = [self._playerid, cmd]
        params = {"id": 1, "method": "slim.request", "params": cmd}
        result = self.get_json(url, params)
        return result

    @staticmethod
    def get_json(url, params):
        '''get info from json api'''
        result = {}
        try:
            response = requests.get(url, data=json.dumps(params), timeout=20)
            if response and response.content and response.status_code == 200:
                result = json.loads(response.content.decode('utf-8', 'replace'))
                if "result" in result:
                    result = result["result"]
            else:
                LOGGER.warning("Invalid or empty reponse from server - server response: %s" %
                        (response.status_code))
        except Exception as exc:
            LOGGER.error("Server is offline or connection error... %s" % exc)

        #log_msg("%s --> %s" %(params, result))
        return result

    def _get_thumb(self, item):
        '''get thumb url from the item's properties'''
        thumb = ""
        if item.get("image"):
            thumb = item["image"]
        elif item.get("icon"):
            thumb = item["icon"]
        elif item.get("icon-id"):
            thumb = item["icon-id"]
        elif item.get("artwork_url"):
            thumb = item["artwork_url"]
        elif item.get("artwork_track_id"):
            thumb = "music/%s/cover.png" % item["artwork_track_id"]
        elif item.get("coverid"):
            thumb = "music/%s/cover.png" % item["coverid"]
        elif item.get("album_id"):
            thumb = "imageproxy/mai/album/%s/image.png" % item["album_id"]
        elif item.get("artist_id"):
            thumb = "imageproxy/mai/artist/%s/image.png" % item["artist_id"]
        elif "album" in item and "id" in item:
            thumb = "imageproxy/mai/album/%s/image.png" % item["id"]
        elif "artist" in item and "id" in item:
            thumb = "imageproxy/mai/artist/%s/image.png" % item["id"]
        elif "window" in item and "icon-id" in item["window"]:
            thumb = item["window"]["icon-id"]

        if thumb and not thumb.startswith("http"):
            server_url = "http://%s:%s" % (self._host, self._port)
            if thumb.startswith("/"):
                thumb = "%s%s" % (server_url, thumb)
            else:
                thumb = "%s/%s" % (server_url, thumb)
        return thumb



class LMSDiscovery(object):
    """Class to discover Logitech Media Servers connected to your network."""

    def __init__(self):
        self.entries = []
        self.last_scan = None
        self._lock = threading.RLock()

    def scan(self):
        """Scan the network for servers."""
        with self._lock:
            self.update()

    def all(self):
        """Scan and return all found entries as a list. Each server is a dict."""
        self.scan()
        return list(self.entries)

    def update(self):
        """update the server netry with details"""
        lms_ip = '<broadcast>'
        lms_port = 3483
        # JSON tag has the port number, it's all we need here.
        lms_msg = "eJSON\0"
        lms_timeout = 5
        entries = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(lms_timeout)
        sock.bind(('', 0))
        try:
            sock.sendto(lms_msg, (lms_ip, lms_port))
            while True:
                try:
                    data, server = sock.recvfrom(1024)
                    host, _ = server
                    if data.startswith(b'E'):
                        port = data.split("\x04")[1]
                        entries.append({'port': int(port),
                                        'data': data,
                                        'from': server,
                                        'host': host})
                except socket.timeout:
                    break
        finally:
            sock.close()
        self.entries = entries