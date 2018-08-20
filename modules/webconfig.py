#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import sys
import time
import threading
import logging
from resources.lib.utils import json, DEVNULL, requests, LOGGER, import_or_install, run_proc


def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_WEBCONFIG", True):
        LOGGER.debug("Webconfig module is not enabled!")
        return False
    import_or_install("flask", ["Flask", "render_template", "flash", "request", "send_file", "redirect", "jsonify"], True, installpip="Flask")
    import_or_install("wtforms", ["StringField", "TextAreaField", "StringField", "SubmitField", "BooleanField", "IntegerField", "FloatField", "SelectField"], True, installpip="WTForms")
    import_or_install("flask_wtf", "FlaskForm", True, installpip="")
    import_or_install("bjoern", installpip="bjoern", installapt="libev-dev python-dev")
    return WebConfig(monitor)



class WebConfig(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.config = monitor.config
        self.monitor = monitor
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        threading.Thread.join(self, 2)

    def run(self):
        this_dir = os.path.dirname(os.path.abspath(__file__))
        root_path = os.path.join(this_dir, "..", "resources/web/")
        app = Flask(__name__, root_path=root_path)
        app.config.from_object(__name__)
        app.config['SECRET_KEY'] = '7d441f27d441f27567d441f2b6176a'

        @app.route('/player_image')
        def player_image():
            player_info = self.monitor.player_info
            if player_info:
                if player_info.get("cover_file"):
                    return send_file(player_info["cover_file"], mimetype='image/png')
                elif player_info.get("cover_art"):
                    return player_info["cover_art"]
                elif player_info.get("cover_url"):
                    if "localhost" in player_info["cover_url"]:
                        response = requests.get(player_info["cover_url"])
                        if response.status_code == 200:
                            return response.content
                    else:
                        return redirect(player_info["cover_url"], code=302)
            # fallback image
            base_dir = os.path.dirname(os.path.abspath(__file__))
            temp_img = os.path.join(base_dir, "..","resources", "web", "static", "default_cover.png")
            return send_file(temp_img, mimetype='image/png')

        @app.route('/states')
        @app.route('/states/<key>')
        @app.route('/states/<key>/<subkey>')
        def get_states(key=None, subkey=None):
            if subkey:
                return json.dumps(self.monitor.states[key][subkey])
            elif key:
                return json.dumps(self.monitor.states[key])
            else:
                return self.monitor.states.json

        @app.route('/command')
        @app.route('/command/<target>')
        @app.route('/command/<target>/<command>')
        def command(target=None, cmd=None):
            if not target:
                target = request.args.get("target")
            if not cmd:
                cmd = request.args.get("command")
            cmd_data = request.args.get("data")
            if cmd and target:
                self.monitor.command(target, cmd, cmd_data)
                flash('Command executed successfully!')
                return "success"
            else:
                flash('Error while executing command')
                return "command is empty"

        @app.route("/log.html")
        def log():
            with open('/tmp/pi-monitor.log') as f:
                data = f.read()
            return render_template('log.html', data=data)

        @app.route("/")
        def status():
            return render_template('status.html', config=self.monitor.config, states=self.monitor.states)

        @app.route("/config.html", methods=['GET', 'POST'])
        def config():
            class ConfigForm(FlaskForm):
                for key, value in self.monitor.config.items():
                    if key == "last_updates":
                        continue
                    label = key.replace("_"," ")
                    if key == "ALSA_VOLUME_CONTROL":
                        choices = [(item, item) for item in self.monitor.states["alsa"]["mixers"]]
                        vars()[key] = SelectField(label=label, choices=choices, id=key, default=value)
                    elif key == "ALSA_SOUND_DEVICE":
                        choices = [(item, item) for item in self.monitor.states["alsa"]["audio_devices"]]
                        vars()[key] = SelectField(label=label, choices=choices, id=key, default=value)
                    elif key == "ALSA_CAPTURE_DEVICE":
                        choices = [(item, item) for item in self.monitor.states["alsa"]["capture_devices"]]
                        vars()[key] = SelectField(label=label, choices=choices, id=key, default=value)
                    elif isinstance(value, (str, unicode)):
                        vars()[key] = StringField(label=label, id=key, default=value)
                    elif isinstance(value, bool):
                        vars()[key] = BooleanField(label=label, id=key, default=value)
                    elif isinstance(value, int):
                        vars()[key] = IntegerField(label=label, id=key, default=value)
                    elif isinstance(value, float):
                        vars()[key] = FloatField(label=label, id=key, default=value)
                    elif isinstance(value, list):
                        values_str = ",".join([str(item) for item in value])
                        vars()[key] = StringField(label=label, id=key, default=values_str)
                    else:
                        LOGGER.warning("unknown type for key %s" % key)
                        vars()[key] = StringField(label=label, id=key, default=value)
            form = ConfigForm()
            print form.errors
            if request.method == 'POST':
                if not form.validate():
                    flash('Error: there is incorrect data in one or more fields!')
                else:
                    # Save the comment here.
                    flash('Changes are saved.')
                    for key, cur_value in self.monitor.config.items():
                        #new_value = request.form[key]
                        new_value = getattr(form, key).data
                        if isinstance(cur_value, list):
                            new_value = new_value.strip()
                            if not new_value:
                                new_value = []
                            else:
                                temp_values = []
                                for value in new_value.split(","):
                                    temp_values.append(int(value.strip()))
                                new_value = temp_values
                        elif isinstance(cur_value, dict):
                            new_value = eval(new_value)
                        if type(new_value) != type(cur_value):
                            LOGGER.error("type mismatch! %s - %s - %s" % (key, type(new_value), type(cur_value)))
                        else:
                            self.monitor.config[key] = new_value
                    self.monitor.command("system", "saveconfig")               
            return render_template('config.html', form=form, config=self.monitor.config, fields=[])

        #app.run(host='0.0.0.0', port=80, debug=False, use_reloader=False)
        bjoern.run(app, '0.0.0.0', 80, reuse_port=True)
        LOGGER.info("exited...")

 
    
 