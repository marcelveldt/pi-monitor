#!/usr/bin/env python

# Copyright (C) 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import print_function

import os.path
from resources.lib.utils import import_or_install, json, PlayerMetaData, PLATFORM
import threading
import sys

try:
    FileNotFoundError
except NameError:
    FileNotFoundError = IOError



def setup(monitor):
    '''setup the module'''
    if not "armv7" in PLATFORM:
        LOGGER.warning("unsupported platform! %s" % PLATFORM)
        return False
    enabled = monitor.config.get("ENABLE_MODULE_GOOGLE_ASSISTANT", False)
    if not enabled:
        LOGGER.debug("Google Assistant module is not enabled!")
        return False
    dummy_mic = monitor.config.get("GOOGLE_ASSISTANT_USE_DUMMY_MIC", False)
    if not dummy_mic and not monitor.states["alsa_capture_devices"]:
        LOGGER.warning("There are no capture devices! Consider using the dummy_mic option for testing purposes")
        return False

    import_or_install("pathlib2", "pathlib", installpip="pathlib2")
    import_or_install("google.assistant.library", "Assistant", True, installpip="google-assistant-library google-assistant-sdk[samples]", installapt="portaudio19-dev libffi-dev libssl-dev")
    import_or_install("google.assistant.library.event", "EventType", True, installpip="google-assistant-sdk[samples]")
    import_or_install("google.assistant.library.file_helpers", "existing_file", True, installpip="google-assistant-sdk[samples]")
    import_or_install("google.assistant.library.device_helpers", "register_device", True, installpip="google-assistant-sdk[samples]")
    import_or_install("google.oauth2.credentials", "Credentials", True, installpip="google-auth-oauthlib[tool]")
    
    model_id="voice-kit-208321-voice-kit-kftedd"
    project_id="voice-kit-208321"
    client_secrets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..","resources", "googlecreds.json")
    credentialsfile = None
    devconfig_file = None
    if dummy_mic:
        os.system("modprobe snd-dummy fake_buffer=0")
    return GoogleAssistantPlayer(credentialsfile, model_id, project_id, devconfig_file, client_secrets, monitor, dummy_mic)



class GoogleAssistantPlayer(threading.Thread):
    _exit = threading.Event()

    def process_event(self, event):
        """Pretty prints events.

        Prints all events that occur with two spaces between each new
        conversation and a single space between turns of a conversation.

        Args:
            event(event.Event): The current event to process.
        """
        LOGGER.debug("Google received event: %s" % event)

        if event.type == EventType.ON_CONVERSATION_TURN_STARTED:
            cur_player = self.monitor.states["player"]["current_player"]
            if cur_player and self.states[cur_player]["state"] in ["playing", "loading"]:
                self.states["player"]["interrupted_player"] = cur_player
                self.monitor.command("player", "stop")
            self.monitor.states["google_assistant"]["state"] = "listening"
            self.monitor.command("player", "ping")
            # TODO: increase volume while assistant is speaking

        elif event.type == EventType.ON_RESPONDING_STARTED:
            LOGGER.debug("ON_RESPONDING_STARTED")
            self.monitor.states["google_assistant"]["state"] = "playing"

        elif event.type == EventType.ON_ALERT_STARTED:
            self.monitor.states["google_assistant"]["state"] = "playing"
        elif event.type in [EventType.ON_ALERT_FINISHED, 
                                EventType.ON_CONVERSATION_TURN_TIMEOUT, 
                                EventType.ON_RESPONDING_FINISHED, 
                                EventType.ON_MEDIA_TRACK_STOP]:
            self.monitor.states["google_assistant"]["state"] = "idle"

        elif event.type == EventType.ON_MEDIA_TRACK_PLAY:
            self.monitor.states["google_assistant"]["state"] = "playing"

        elif event.type == EventType.ON_CONVERSATION_TURN_FINISHED:
            if event.args and event.args['with_follow_on_turn']:
                self.monitor.states["google_assistant"]["state"] = "listening"
            else:
                self.monitor.states["google_assistant"]["state"] = "idle"
                if self.states["player"].get("interrupted_player"):
                    self.monitor.command(self.states["player"]["interrupted_player"], "command", "play")
                    self.states["player"]["interrupted_player"] = ""

        
        elif event.type == EventType.ON_DEVICE_ACTION:
            for command, params in event.actions:
                LOGGER.info('Do command', command, 'with params', str(params))

    def authenticate_device(self):
        import google_auth_oauthlib.flow
        scopes = ["https://www.googleapis.com/auth/assistant-sdk-prototype", "https://www.googleapis.com/auth/gcm"]
        self.monitor.config["GOOGLE_ASSISTANT_AUTH_CODE"] = ""
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
            self.client_secrets,
            scopes=scopes
        )
        flow.redirect_uri = flow._OOB_REDIRECT_URI
        auth_url, _ = flow.authorization_url()
        LOGGER.info("######################################################################################")
        LOGGER.info("#        Registering Google Assistant                                                #")
        LOGGER.info('#        Please visit the url below in your browser and                              #')
        LOGGER.info('#        paste the resulting code in the web configuration                           #')
        LOGGER.info('#        There will be a new setting added, called "GOOGLE ASSISTANT AUTH CODE"      #')
        LOGGER.info('#                                                                                    #')
        LOGGER.info(' ')
        LOGGER.info(' %s' % auth_url)
        LOGGER.info(' ')
        LOGGER.info("######################################################################################")

        code = None
        while not code and not self._exit.is_set():
            code = self.monitor.config["GOOGLE_ASSISTANT_AUTH_CODE"]
        
        if code:
            flow.fetch_token(code=code)
            LOGGER.info("Device is registered succesfully!")
            self.monitor.config["GOOGLE_ASSISTANT_AUTH_CODE"] = ""
            creds = flow.credentials
            creds_data = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
            del creds_data['token']
            config_path = os.path.dirname(self.credentialsfile)
            if not os.path.isdir(config_path):
                os.makedirs(config_path)
            with open(self.credentialsfile, 'w') as outfile:
                json.dump(creds_data, outfile)
            LOGGER.debug("Credentials saved to %s" % self.credentialsfile)

    def __init__(self, credentialsfile=None, model_id=None, project_id=None, devconfig_file=None, client_secrets=None, monitor=None, mic_muted=False):
        if not credentialsfile:
            credentialsfile = os.path.join(os.path.expanduser('~/.config'), 'google-oauthlib-tool','credentials.json')
        self.credentialsfile = credentialsfile
        if not devconfig_file:
            devconfig_file = os.path.join(os.path.expanduser('~/.config'), 'googlesamples-assistant','device_config_library.json')
        device_model_id = None
        last_device_id = None
        try:
            with open(devconfig_file) as f:
                device_config = json.load(f)
                device_model_id = device_config['model_id']
                last_device_id = device_config.get('last_device_id', None)
        except FileNotFoundError:
            pass
        if not model_id and not device_model_id:
            raise Exception('Missing --device-model-id option')
        # Re-register if "device_model_id" is given by the user and it differs
        # from what we previously registered with.
        should_register = (
            model_id and model_id != device_model_id)
        self.device_model_id = model_id or device_model_id
        self.devconfig_file = devconfig_file
        self.last_device_id = last_device_id
        self.project_id = project_id
        self.should_register = should_register
        self.mic_muted = mic_muted
        self.monitor = monitor
        self.client_secrets = client_secrets
        if monitor:
            self.monitor.states["google_assistant"] = PlayerMetaData("Google Assistant")
        threading.Thread.__init__(self)

    def stop(self):
        self._exit.set()
        if not os.path.isfile(self.credentialsfile):
            return
        with Assistant(self.credentials, self.device_model_id) as assistant:
            assistant.send_text_query("exit")
        threading.Thread.join(self, 10)

    def run(self):
        if not os.path.isfile(self.credentialsfile):
            # we should authenticate
            self.authenticate_device()
        if not os.path.isfile(self.credentialsfile):
            return
        with open(self.credentialsfile, 'r') as f:
            self.credentials = Credentials(token=None, **json.load(f))

        with Assistant(self.credentials, self.device_model_id) as assistant:
            events = assistant.start()
            assistant.set_mic_mute(self.mic_muted)
            device_id = assistant.device_id
            print('device_model_id:', self.device_model_id)
            print('device_id:', device_id + '\n')

            # Re-register if "device_id" is different from the last "device_id":
            if self.should_register or (device_id != self.last_device_id):
                if self.project_id:
                    register_device(self.project_id, self.credentials,
                                    self.device_model_id, device_id)
                    pathlib.Path(os.path.dirname(self.devconfig_file)).mkdir(exist_ok=True)
                    with open(self.devconfig_file, 'w') as f:
                        json.dump({
                            'last_device_id': device_id,
                            'model_id': self.device_model_id,
                        }, f)
                else:
                    LOGGER.error("Device is not registered!")

            for event in events:
                if self._exit.is_set():
                    return
                self.process_event(event)
