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
    import_or_install("wtforms", ["TextField", "TextAreaField", "StringField", "SubmitField", "BooleanField", "IntegerField", "FloatField", "SelectField"], True, installpip="WTForms")
    import_or_install("flask_wtf", "FlaskForm", True, installpip="")
    return WebConfig(monitor)



class WebConfig(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.config = monitor.config
        self.monitor = monitor
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        run_proc("curl http://localhost/shutdown")
        threading.Thread.join(self, 10)

    def run(self):
        this_dir = os.path.dirname(os.path.abspath(__file__))
        root_path = os.path.join(this_dir, "..", "resources/web/")
        app = Flask(__name__, root_path=root_path)
        app.config.from_object(__name__)
        app.config['SECRET_KEY'] = '7d441f27d441f27567d441f2b6176a'
        logging.getLogger('werkzeug').setLevel(logging.ERROR)

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

        @app.route('/player_info.json')
        def player_info_json():
            return self.monitor.player_info.json

        @app.route('/command')
        def command():
            target = request.args.get("target")
            cmd = request.args.get("command")
            cmd_data = request.args.get("data")
            if cmd and target:
                self.monitor.command(target, cmd, cmd_data)   
                return "success"
            else:
                return "command is empty"

        @app.route('/shutdown', methods=['POST', 'GET'])
        def shutdown():
            func = request.environ.get('werkzeug.server.shutdown')
            if func is None:
                raise RuntimeError('Not running with the Werkzeug Server')
            func()
            return 'Server shutting down...'

        @app.route('/spotify/_zeroconf', methods=['GET', 'POST'])
        def spotify_zeroconf():
            LOGGER.info("spotify_zeroconf: %s" % request.args)
            action = request.args.get('action') or request.form.get('action')
            if not action:
                return jsonify({
                    'status': 301,
                    'spotifyError': 0,
                    'statusString': 'ERROR-MISSING-ACTION'})
            if action == 'getInfo' and request.method == 'GET':
                return jsonify(self.monitor.states["spotify"]["zeroconf"])
            elif action == 'addUser' and request.method == 'POST':
                args = request.form

                username = str(args.get('userName'))
                blob = str(args.get('blob'))
                clientKey = str(args.get('clientKey'))
                data = {
                    "username": username,
                    "blob": blob,
                    "clientKey": clientKey
                }
                self.monitor.command("spotify", "login", data)
                #connect_app.login(username, zeroconf=(blob,clientKey))
                return jsonify({
                    'status': 101,
                    'spotifyError': 0,
                    'statusString': 'ERROR-OK'
                    })
            else:
                return jsonify({
                    'status': 301,
                    'spotifyError': 0,
                    'statusString': 'ERROR-INVALID-ACTION'})

        @app.route('/spotify/_zeroconf_vars', methods=['GET', 'POST'])
        def spotify_zeroconf_vars():
            content = request.get_json()
            LOGGER.info("spotify_zeroconf_vars: %s" % content)
            self.monitor.states["spotify"]["zeroconf"] = json.loads(content)
            return "OK"

        def get_logs():
            with open('/tmp/pi-monitor.log') as f:
                data = f.read()
            return data

        @app.route("/", methods=['GET', 'POST'])
        def config():
            class ConfigForm(FlaskForm):
                for key, value in self.monitor.config.items():
                    if key == "last_updates":
                        continue
                    label = key.replace("_"," ")
                    if key == "ALSA_VOLUME_CONTROL":
                        choices = [(item, item) for item in self.monitor.states["alsa_mixers"]]
                        vars()[key] = SelectField(label=label, choices=choices, id=key, default=value)
                    elif key == "ALSA_SOUND_DEVICE":
                        choices = [(item, item) for item in self.monitor.states["alsa_devices"]]
                        vars()[key] = SelectField(label=label, choices=choices, id=key, default=value)
                    elif isinstance(value, (str, unicode)):
                        vars()[key] = TextField(label=label, id=key, default=value)
                    elif isinstance(value, bool):
                        vars()[key] = BooleanField(label=label, id=key, default=value)
                    elif isinstance(value, int):
                        vars()[key] = IntegerField(label=label, id=key, default=value)
                    elif isinstance(value, float):
                        vars()[key] = FloatField(label=label, id=key, default=value)
                    elif isinstance(value, list):
                        values_str = ",".join([str(item) for item in value])
                        vars()[key] = TextField(label=label, id=key, default=values_str)
                    else:
                        LOGGER.warning("unknown type for key %s" % key)
                        vars()[key] = TextField(label=label, id=key, default=value)
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
            return render_template('config.html', form=form)

        app.run(host='0.0.0.0', port=80, debug=False, use_reloader=False)
        LOGGER.info("exited...")

 
    
 