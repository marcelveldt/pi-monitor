#!/usr/bin/env python

import os
from resources.lib.utils import import_or_install, VOLUME_CONTROL_DISABLED, VOLUME_CONTROL_SOFT


def setup(monitor):
    '''setup the module'''
    import_or_install("alsaaudio", installapt="libasound2-dev", installpip="pyalsaaudio")
    return AlsaVolume(monitor)


class AlsaVolume(object):
    _exit = False

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["alsa"] = {
                "alsa_devices": [],
                "alsa_capture_devices": [],
                "alsa_mixers": []
            }
        self._setup_alsa_config()
        self._mixer = alsaaudio.Mixer(self.monitor.config["ALSA_VOLUME_CONTROL"])
        self.monitor.states["player"]["volume_level"] = self._volume_get()
        LOGGER.info("current alsa volume level: %s" % self._volume_get())
        
    def stop(self):
        self._exit = True

    def start(self):
        pass

    def command(self, cmd, cmd_data=None):
        ''' send command to roon output/zone'''
        if cmd == "volume_up":
            return self._volume_up()
        elif cmd == "volume_down":
            return self._volume_down
        elif cmd == "volume_set":
            return self._volume_set(cmd_data)
        else:
            return False

    def _volume_up(self):
        cur_vol = self._volume_get()
        self._volume_set(cur_vol + 2)
        return True

    def _volume_down(self):
        cur_vol = self._volume_get()
        self._volume_set(cur_vol - 2)
        return True

    def _volume_set(self, volume_level):
        ''' set volume level '''
        self._mixer.setvolume(int(volume_level), alsaaudio.PCM_PLAYBACK)
        self.monitor.states["player"]["volume_level"] = volume_level
        return True

    def _volume_get(self):
        ''' get current volume level of player'''
        return self._mixer.getvolume(alsaaudio.PCM_PLAYBACK)

    def _setup_alsa_config(self):
        ''' get details about the alsa configuration'''

        selected_mixer = self.monitor.config.get("ALSA_VOLUME_CONTROL", "") # value in stored config, if any
        selected_audio_device = self.monitor.config.get("ALSA_SOUND_DEVICE", "") # value in stored config, if any
        selected_capture_device = self.monitor.config.get("ALSA_CAPTURE_DEVICE", "") # value in stored config, if any

        # get playback devices
        default_audio_device = ""
        alsa_devices = []
        selected_audio_device_found = False
        for dev in alsaaudio.pcms(alsaaudio.PCM_PLAYBACK):
            # we only care about direct hardware access so we filter out the dsnoop stuff etc
            if dev.startswith("hw:") or dev.startswith("plughw:") and "Dummy" not in dev:
                dev = dev.replace("CARD=","").split(",DEV=")[0]
                alsa_devices.append(dev)
                if selected_audio_device and dev == selected_audio_device:
                    selected_audio_device_found = True
                if not default_audio_device:
                    default_audio_device = dev
        if not selected_audio_device_found:
            selected_audio_device = default_audio_device
        self.monitor.states["alsa_devices"] = alsa_devices

        # get capture devices
        default_capture_device = ""
        alsa_capture_devices = []
        selected_capture_device_found = False
        for dev in alsaaudio.pcms(alsaaudio.PCM_CAPTURE):
            if dev.startswith("hw:") or dev.startswith("plughw:"):
                dev = dev.replace("CARD=","").split(",DEV=")[0]
                alsa_capture_devices.append(dev)
                if selected_capture_device and dev == selected_capture_device:
                    selected_capture_device_found = True
                if not default_capture_device:
                    default_capture_device = dev
        if not default_capture_device:
            # create dummy recording device
            os.system("modprobe snd-dummy fake_buffer=0")
            default_capture_device = "hw:Dummy"
            alsa_capture_devices.append(default_capture_device)
        if not selected_capture_device_found:
            selected_capture_device = default_capture_device
        self.monitor.states["alsa_capture_devices"] = alsa_capture_devices
        
        # only lookup mixers for the selected audio device
        # TODO: extend this selection criteria with more use cases
        default_mixer = ""
        alsa_mixers = []
        selected_mixer_found = False
        for mixer in alsaaudio.mixers(device=selected_audio_device):
            if mixer == "Digital":
                default_mixer = u"Digital"
            elif mixer == "PCM":
                default_mixer = u"PCM"
            elif mixer == "Analog":
                default_mixer = u"Analog"
            elif mixer == "Lineout volume control":
                default_mixer = u"Lineout volume control"
            elif mixer == "Master":
                default_mixer = u"Master"
            elif mixer == "SoftMaster":
                default_mixer = u"SoftMaster"
            alsa_mixers.append(mixer)
            if mixer == selected_mixer:
                selected_mixer_found = True
        # append softvol and no volume control
        if not alsa_mixers or not default_mixer:
            alsa_mixers.append(VOLUME_CONTROL_SOFT)
            if "digi" in selected_audio_device:
                # assume digital output
                default_mixer = VOLUME_CONTROL_DISABLED
            else:
                default_mixer = VOLUME_CONTROL_SOFT
        alsa_mixers.append(VOLUME_CONTROL_DISABLED)
        if not selected_mixer_found:
            # set default mixer as selected mixer
            selected_mixer = default_mixer
        self.monitor.states["alsa_mixers"] = alsa_mixers
        
        # write default asound file - is needed for volume control and google assistant to work properly
        if selected_mixer == VOLUME_CONTROL_SOFT:
            # alsa conf with softvol
            alsa_conf = '''
            pcm.softvol {
                  type softvol
                  slave.pcm hw:%s
                  control {
                    name "%s"
                    card %s
                  }
                  min_dB -90.2
                  max_dB 3.0
                }
            pcm.!default {
                type asym
                 playback.pcm {
                   type plug
                   slave.pcm "softvol"
                 }
                 capture.pcm {
                   type plug
                   slave.pcm hw:%s
                 }
              }
              ctl.softvol { 
                  type hw 
                  card %s 
              }
            ''' % (selected_audio_device.split(":")[-1], VOLUME_CONTROL_SOFT, selected_audio_device.split(":")[-1], selected_capture_device.split(":")[-1], selected_audio_device.split(":")[-1])
            selected_audio_device = "softvol"
        else:
            # alsa conf without softvol
            alsa_conf = '''
            pcm.!default {
                type asym
                 playback.pcm {
                   type plug
                   slave.pcm hw:%s
                 }
                 capture.pcm {
                   type plug
                   slave.pcm hw:%s
                 }
              }
              defaults.ctl.playback.device hw:%s
            ''' % (selected_audio_device.split(":")[-1], selected_capture_device.split(":")[-1],selected_audio_device.split(":")[-1])
        # write file
        with open("/etc/asound.conf", "w") as f:
            f.write(alsa_conf)
        # print some logging and set the config values
        LOGGER.debug("alsa playback devices: %s - default_audio_device: %s - selected_audio_device: %s" % (str(alsa_devices), default_audio_device, selected_audio_device))
        LOGGER.debug("alsa recording devices: %s - default_capture_device: %s - selected_capture_device: %s" % (str(alsa_capture_devices), default_capture_device, selected_capture_device))
        LOGGER.debug("alsa mixers: %s - default_capture_device: %s - selected_capture_device: %s" % (str(alsa_mixers), default_mixer, selected_mixer))
        self.monitor.config["ALSA_SOUND_DEVICE"] = selected_audio_device
        self.monitor.config["ALSA_VOLUME_CONTROL"] = selected_mixer
        self.monitor.config["ALSA_CAPTURE_DEVICE"] = selected_capture_device

