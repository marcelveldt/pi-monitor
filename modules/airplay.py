#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import sys
import time
import threading
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, requests, import_or_install, global_import, check_software, run_proc, HOSTNAME

LOOP_WAIT = 2

EXEC_BIN = "/usr/local/bin/shairport-sync"
EXEC_CONF = "/tmp/shairport-sync.conf"
EXEC_FIFO = "/tmp/shairport-sync-metadata"

def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_AIRPLAY", False):
        LOGGER.debug("Airplay module is not enabled!")
        return False

    if not check_software(dietpi_id="37", bin_path=EXEC_BIN):
        LOGGER.warning("shairport-sync is not installed, please install manually.")
        return False

    import_or_install("DictObject")
    import_or_install("magic", installpip="python-magic")
    global_import("resources.lib.shairportdecoder.remote", "AirplayRemote", True)
    global_import("resources.lib.shairportdecoder.decoder", ["Processor", "VOLUME", "COVERART", "META", "CLIENT_REMOTE_AVAILABLE"], True)
    global_import("resources.lib.shairportdecoder.metadata", "Infos", True)

    return AirPlayPlayer(monitor)


class AirPlayPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = "stopped"
    _processor = None
    _remote = None
    _fifo_buffer = None
    _shairport_proc = None

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["airplay"] = PlayerMetaData("Airplay")
        config_modified = False
        run_proc("service shairport-sync stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        run_proc("service shairport-sync stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        if self._shairport_proc:
            self._shairport_proc.terminate()
        with open(EXEC_FIFO, 'w') as f:
            f.write("####STOP####\n")
            f.write("####STOP####\n")
        if self._remote:
            del self._remote
            self._remote = None
        threading.Thread.join(self, 10)

    def command(self, cmd, cmd_data=None):
        ''' send command to airplay output/zone'''
        if not self._remote:
            return False
        if cmd == "next":
            cmd = "nextitem"
        elif cmd == "previous":
            cmd = "previtem"
        elif cmd == "toggleplaypause":
            cmd = "playpause"
        elif cmd == "volume_up":
            self._remote.volume_up()
        elif cmd == "volume_down":
            self._remote.volume_down()
        elif cmd == "volume_set":
            return False
        return self._remote.do(cmd)

    def _event_processor(self, event_type, info):
        assert(isinstance(info, Infos))
        self._update_metadata()
        if event_type == VOLUME:
            LOGGER.debug("Changed Volume to {vol}.".format(vol = info.volume))
        elif event_type == COVERART:
            LOGGER.debug("Retrieved CoverArt - saved to %s" % self._processor.info.cover_file)
        elif event_type == META:
            LOGGER.debug("Got Metadata: %s" % info.to_simple_string().encode("utf-8")) # lol, meat typo.
        elif event_type == CLIENT_REMOTE_AVAILABLE:
            LOGGER.debug("Got Airplay Remote informations.")
            self._remote = AirplayRemote.from_dacp_id(self._processor.info.dacp_id, self._processor.info.active_remote)
        else:
            LOGGER.debug("event_type: %s" % event_type)

    def _update_metadata(self):
        self.monitor.states["airplay"]["volume_level"] = self._processor.info.volume * 100 if self._processor.info.volume else 0
        self.monitor.states["airplay"]["state"] = self._processor.info.playstate
        self.monitor.states["airplay"]["artist"] = self._processor.info.songartist
        self.monitor.states["airplay"]["album"] = self._processor.info.songalbum
        self.monitor.states["airplay"]["title"] = self._processor.info.itemname
        self.monitor.states["airplay"]["duration"] = self._processor.info.songtime/1000 if self._processor.info.songtime else 0
        if self._processor.info.cover_file:
            self.monitor.states["airplay"]["cover_file"] = self._processor.info.cover_file
        elif self._processor.info.songcoverart.base64:
            self.monitor.states["airplay"]["cover_art"] = self._processor.info.songcoverart.base64
        else:
            self.monitor.states["airplay"]["cover_file"] = ""
            self.monitor.states["airplay"]["cover_art"] = ""

    def _create_config(self):
        # create shairport sync config
        config_text = '''
        general =
        {
          name = "%s";
          interpolation = "soxr";
          output_backend = "alsa"; 
        };
        metadata =
        {
            enabled = "yes";
            include_cover_art = "yes";
            pipe_name = "%s";
            pipe_timeout = 5000;
        };
        alsa =
        {
            output_device = "%s";
            mixer_control_name = "%s";
        };
        ''' % (HOSTNAME, EXEC_FIFO, self.monitor.config["ALSA_SOUND_DEVICE"], self.monitor.config["ALSA_VOLUME_CONTROL"])
        with open(EXEC_CONF, "w") as f:
            f.write(config_text)


    def run(self):
        self._create_config()
        args = [ EXEC_BIN, "-c", EXEC_CONF ]
        if self.monitor.config["ENABLE_DEBUG"]:
            LOGGER.debug("Starting shairport-sync: %s" % " ".join(args))
            self._shairport_proc = subprocess.Popen(args)
        else:
            self._shairport_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)
        # start watching the metadata through the named pipe
        self._processor = Processor()
        self._processor.add_listener(self._event_processor)
        LOGGER.info("Start Parsing named pipe: %s" % EXEC_FIFO)
        self._fifo_buffer = open(EXEC_FIFO)
        temp_line = ""
        while not self._exit.isSet():
            line = self._fifo_buffer.readline()
            if len(line) == 0 or "STOP" in line:
                break
            if not line.strip().endswith("</item>"):
                temp_line += line.strip()
                continue
            line = temp_line + line
            temp_line = ""
            if not self._exit.isSet():
                self._processor.process_line(line)
            self._exit.wait(0.5)
        