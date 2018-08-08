# -*- coding: utf-8 -*-

__author__ = 'luckydonald'

from resources.lib.utils import LOGGER, try_encode, try_decode
import requests
import os
import threading
import time 
from resources.lib.zeroconf import ServiceBrowser, Zeroconf

airplay_zeroconf_service = "_dacp._tcp.local."  # local?
airplay_prefix = "iTunes_Ctrl_{dacp_id}"
base_url = "{host}:{port}/ctrl-int/1/{command}"


class AirplayRemote(object):
	"""
	GET /ctrl-int/1/pause HTTP/1.1
	Active-Remote: 1986535575
	"""

	def __init__(self,  token, host, port):
		super(AirplayRemote, self).__init__()
		self.token = token
		self.host = host
		self.port = port

	@classmethod
	def from_dacp_id(cls, dacp_id, token):
		zeroconf = Zeroconf()
		try:
			listener = ServiceListener(dacp_id, zeroconf)
			browser = ServiceBrowser(zeroconf, airplay_zeroconf_service, listener)
			wait_for_it = ResultWaiter(listener, browser)
			wait_for_it.start()
			wait_for_it.join(2)
			del wait_for_it
		finally:
			zeroconf.close()
		if listener and listener.info:
			ip_str = ".".join([str(ord(x)) for x in listener.info.address])
			host = "http://%s" % ip_str
			port = listener.info.port
			return AirplayRemote(token, host, port)
		else:
			LOGGER.error("listener is empty!")
			return None

	def begin_fast_forward(self):
		"""
		begin fast forward
		:return: None
		"""
		return self.do("beginff")

	def begin_rewind(self):
		"""
		begin rewind
		:return: None
		"""
		return self.do("beginrew")

	def previous_item(self):
		"""
		play previous item in playlist
		:return: None
		"""
		return self.do("previtem")

	def next_item(self):
		"""
		play next item in playlist
		:return: None
		"""
		return self.do("nextitem")

	def pause(self):
		"""
		pause playback
		:return: None
		"""
		return self.do("pause")

	def play_pause(self):
		"""
		toggle between play and pause
		:return: None
		"""
		return self.do("playpause")

	def play(self):
		"""
		start playback
		:return: None
		"""
		return self.do("play")

	def stop(self):
		"""
		stop playback
		:return: None
		"""
		return self.do("stop")

	def play_resume(self):
		"""
		play after fast forward or rewind
		:return: None
		"""
		return self.do("playresume")

	def shuffle_songs(self):
		"""
		shuffle playlist
		:return: None
		"""
		return self.do("shuffle_songs")

	def volume_down(self):
		"""
		turn audio volume down
		:return: None
		"""
		return self.do("volumedown")

	def volume_up(self):
		"""
		turn audio volume up
		:return: None
		"""
		return self.do("volumeup")


	def do(self, command):
		"""
		Send a request to the api.

		:param action:
		:param data:
		:param query:
		:return:
		"""
		headers = {"Active-Remote": self.token, "Host": "starlight.local"}
		url = base_url.format(command=try_encode(command), host=self.host, port=self.port)
		r = requests.get(url, headers=headers, verify=False)  # Allow unsigned certificates.
		return r



class ResultWaiter(threading.Thread):
	def i_am_a_callback(self, *args, **kwargs):
		self.args = args
		self.kwargs = kwargs
		self.callback_callback()


	def __init__(self, listener, browser):#callback_callback):
		super(ResultWaiter, self).__init__()
		#self.callback_callback = callback_callback
		self.listener = listener
		self.browser = browser

	def run(self):
		while True:
			if self.listener.info:
				#self.browser.cancel()
				break
			time.sleep(1)


class ServiceListener(object):
	def __init__(self, dacp_id, zeroconf):
		super(ServiceListener, self).__init__()
		self.dacp_id = dacp_id
		self.zeroconf = zeroconf
		self.info = None

	def remove_service(self, zeroconf, type, name):
		LOGGER.debug("Service %s removed" % (name,))

	def add_service(self, zeroconf, type, name):
		info = zeroconf.get_service_info(type, name)
		if self.dacp_id in name:
			self.info = info
			LOGGER.debug("Found Airplay remote client: {name}, {data}".format(name=name, data=info))
		else:
			LOGGER.debug("Wrong Airplay Service %s found, service info: %s" % (name, info))

