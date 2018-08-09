#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
from resources.lib.utils import import_or_install, json

def setup(monitor):
    ''' setup the module'''

    if not monitor.config.get("ENABLE_MODULE_MQTT", False):
        LOGGER.debug("module is not enabled")
        return False
    # default config entries init
    mqtt_topic_base = u"%hostname%"
    mqtt_client = monitor.config.get("MQTT_CLIENT_ID", u"pi_monitor-%hostname%")
    monitor.config.get("MQTT_HOST", u"192.168.1.1")
    monitor.config.get("MQTT_PORT", 1883)
    monitor.config.get("MQTT_USERNAME", u"")
    monitor.config.get("MQTT_PASSWORD", u"")
    monitor.config.get("MQTT_TOPIC_COMMAND", u"%s/cmd" % mqtt_topic_base)
    monitor.config.get("MQTT_TOPIC_STAT", u"%s/stat" % mqtt_topic_base)
    monitor.config.get("MQTT_QOS", 1)
    monitor.config.get("MQTT_RETAIN", False)
    monitor.config.get("MQTT_CLEAN_SESSION", False)
    monitor.config.get("MQTT_LWT", u"clients/%s" % mqtt_client)
    # conditional import of globals
    import_or_install("paho.mqtt.client", "Client", True, installpip="paho-mqtt")
    return MQTT(monitor)


class MQTT(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.monitor = monitor
        self.config = monitor.config
        LOGGER.info("Publish Stats to topic: %s/<target>" % self.config["MQTT_TOPIC_STAT"] )
        LOGGER.info("Listen for Commands on topic: %s/<target>/<command>" % self.config["MQTT_TOPIC_COMMAND"] )
        # Create the MQTT client
        self._mqttc = Client(self.config["MQTT_CLIENT_ID"], clean_session=self.config["MQTT_CLEAN_SESSION"])
        threading.Thread.__init__(self)

    def run(self):
        # Connect to broker and keep thread alive
        self._connect()
        # register state changed events
        self.monitor.register_state_callback(self.state_event)
        while not self._exit.isSet():
            self._exit.wait(3600) # keep thread alive

    def state_event(self, key, value, subkey=None):
        ''' callback if one of the states changes we are listening for'''
        if not key in self.monitor.states:
            return
        value = self.monitor.states[key]
        topic = "%s/%s" %(self.config["MQTT_TOPIC_STAT"], key)
        try:
            value = json.dumps(value)
        except:
            pass
        self.publish(topic, value, retain=False)
        
    def stop(self):
        self._exit.set()
        self.publish(self.config["MQTT_LWT"], "0", qos=0, retain=True)
        self._mqttc.disconnect()
        self._mqttc.loop_stop()
        threading.Thread.join(self, 10)

    def publish(self, topic, value, qos=None, retain=None):
        LOGGER.debug("publish %s to topic %s" %(value, topic))
        if qos == None:
            qos = self.config["MQTT_QOS"]
        if retain == None:
            retain = self.config["MQTT_RETAIN"]
        return self._mqttc.publish(topic, value, qos=qos, retain=retain)

    def _on_connect(self, mosq, obj, flags, result_code):
        """
        Handle connections (or failures) to the broker.
        This is called after the client has received a CONNACK message
        from the broker in response to calling connect().
        """
        if result_code == 0:
            LOGGER.info("Connected to %s:%s" % (self.config["MQTT_HOST"], self.config["MQTT_PORT"]))
            # Subscribe only to command topic
            _topic = self.config["MQTT_TOPIC_COMMAND"] + "/#"
            LOGGER.info("subscribe to command topic: %s" % _topic)
            self._mqttc.message_callback_add(_topic, self._on_message)

            # Publish retained LWT as per http://stackoverflow.com/questions/19057835/how-to-find-connected-mqtt-client-details/19071979#19071979
            self._mqttc.publish(self.config["MQTT_LWT"], "1", qos=0, retain=True)
        elif result_code == 1:
            LOGGER.info("Connection refused - unacceptable protocol version")
        elif result_code == 2:
            LOGGER.info("Connection refused - identifier rejected")
        elif result_code == 3:
            LOGGER.info("Connection refused - server unavailable")
        elif result_code == 4:
            LOGGER.info("Connection refused - bad user name or password")
        elif result_code == 5:
            LOGGER.info("Connection refused - not authorised")
        else:
            LOGGER.warning("Connection failed - result code %d" % (result_code))

    def _on_disconnect(self, mosq, obj, result_code):
        """
        Handle disconnections from the broker
        """
        if result_code == 0:
            LOGGER.info("Clean disconnection from broker")
        else:
            LOGGER.info("Broker connection lost. Retrying in 5s...")
            time.sleep(5)

    def _on_message(self, mosq, obj, msg):
        """
        Handle incoming messages
        """
        LOGGER.debug("Received MQTT message --> topic: %s - payload: %s" % (msg.topic, msg.payload.decode("utf-8")))
        topicparts = msg.topic.split("/")
        # some magic to found out the target and command
        if "/".join(topicparts[:-1]) == self.config["MQTT_TOPIC_COMMAND"]:
            target = topicparts[-1]
            command = msg.payload
        elif "/".join(topicparts[:-2]) == self.config["MQTT_TOPIC_COMMAND"]:
            target = topicparts[-2]
            command = topicparts[-1]
            
        else:
            target = None
            command = None
        if not target or not command:
            LOGGER.warning("received command in invalid format !")
        else:
            opt_data = msg.payload
            try:
                opt_data = eval(opt_data)
            except:
                pass
            LOGGER.debug("Processing command --> target: %s - command: %s - opt_data: %s" % (target, command, opt_data))
            self.monitor.command(target, command, opt_data)


    def _connect(self):
        """
        Connect to the broker, define the callbacks, and subscribe
        This will also set the Last Will and Testament (LWT)
        The LWT will be published in the event of an unclean or
        unexpected disconnection.
        """

        LOGGER.info("Connect to MQTT broker...")

        # Add the callbacks
        self._mqttc.on_connect = self._on_connect
        self._mqttc.on_disconnect = self._on_disconnect

        # Set the login details
        if self.config["MQTT_USERNAME"]:
            self._mqttc.username_pw_set(self.config["MQTT_USERNAME"], self.config["MQTT_PASSWORD"])
        
        # Set the Last Will and Testament (LWT) *before* connecting
        self._mqttc.will_set(self.config["MQTT_LWT"], payload="0", qos=0, retain=True)

        # Attempt to connect
        mqtt_host = self.config["MQTT_HOST"]
        mqtt_port = self.config["MQTT_PORT"]
        while True:
            LOGGER.debug("Connecting to %s:%d..." % (mqtt_host, mqtt_port))
            try:
                self._mqttc.connect(mqtt_host, mqtt_port, 60)
                break
            except Exception as e:
                LOGGER.error("Error connecting to %s:%d: %s" % (mqtt_host, mqtt_port, str(e)))
                time.sleep(5)
            
        # Let the connection run forever
        self._mqttc.loop_start()
