# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
import octoprint.settings
import octoprint.util
import subprocess
import datetime
import os


from .discord import DiscordMessage
from .events import EVENTS, CATEGORIES
from .media import Media


class OctorantPlugin(
	octoprint.plugin.EventHandlerPlugin,
	octoprint.plugin.StartupPlugin,
	octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
	octoprint.plugin.ProgressPlugin
):

	def __init__(self):
		# Events definition here (better for intellisense in IDE)
		# referenced in the settings too.
		self.events = EVENTS
		self.uploading = False

		
	def on_after_startup(self):
		self._logger.info("OctoRant is started !")


	##~~ SettingsPlugin mixin

	def get_settings_defaults(self):
		return {
			'url': "",
			'username': "",
			'avatar': "",
			'categories': CATEGORIES,
			'events' : self.events,
			'allow_scripts': False,
			'script_before': '',
			'script_after': ''
		}

	# Restricts some paths to some roles only
	def get_settings_restricted_paths(self):
		# settings.events.tests is a false message, so we should never see it as configurable.
		# settings.url, username and avatar are admin only.
		return dict(never=[["events","test"]],
					admin=[["url"],["username"],["avatar"],['script_before'],['script_after']])

	##~~ AssetPlugin mixin

	def get_assets(self):
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js=["js/octorant.js"],
			css=["css/octorant.css"]
		)


	##~~ TemplatePlugin mixin
	def get_template_configs(self):
		return [
			dict(type="settings", custom_bindings=False)
		]

	##~~ Softwareupdate hook

	def get_update_information(self):
		# Define the configuration for your plugin to use with the Software Update
		# Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
		# for details.
		return dict(
			octorant=dict(
				displayName="OctoRant Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="bchanudet",
				repo="OctoPrint-Octorant",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/bchanudet/OctoPrint-Octorant/archive/{target_version}.zip"
			)
		)

	##~~ Settings hook	
	def on_settings_save(self, data):
		old_bot_settings = '{}{}{}'.format(\
			self._settings.get(['url'],merged=True),\
			self._settings.get(['avatar'],merged=True),\
			self._settings.get(['username'],merged=True)\
		)
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
		new_bot_settings = '{}{}{}'.format(\
			self._settings.get(['url'],merged=True),\
			self._settings.get(['avatar'],merged=True),\
			self._settings.get(['username'],merged=True)\
		)
	
		if(old_bot_settings != new_bot_settings):
			self._logger.info("Settings have changed. Send a test message...")
			self.notify_event("test")


	##~~ EventHandlerPlugin hook
	def on_event(self, event, payload):
		
		if event == "Startup":
			return self.notify_event("startup")
		
		if event == "Shutdown":
			return self.notify_event("shutdown")
		
		if event == "PrinterStateChanged":
			if payload["state_id"] == "OPERATIONAL":
				return self.notify_event("printer_state_operational")
			elif payload["state_id"] == "ERROR":
				return self.notify_event("printer_state_error")
			elif payload["state_id"] == "UNKNOWN":
				return self.notify_event("printer_state_unknown")
			else:
				self._logger.debug("Event {}/{} was not handled".format(event, payload["state_id"]))
		
		if event == "PrintStarted":
			return self.notify_event("printing_started",payload)	
		if event == "PrintPaused":
			return self.notify_event("printing_paused",payload)
		if event == "PrintResumed":
			return self.notify_event("printing_resumed",payload)
		if event == "PrintCancelled":
			return self.notify_event("printing_cancelled",payload)

		if event == "PrintDone":
			payload['time_formatted'] = str(datetime.timedelta(seconds=int(payload["time"])))
			return self.notify_event("printing_done", payload)

		if event == "TransferStarted":
			self.notify_event("transfer_started", payload)
			self.uploading = True
			return True
		if event == "TransferDone":
			payload['time_formatted'] = str(datetime.timedelta(seconds=int(payload["time"])))
			self.notify_event("transfer_done", payload)
			self.uploading = False
			return True
		if event == "TransferFailed":
			self.notify_event("transfer_failed", payload)
			self.uploading = False
			return True
		if event == "MovieDone":
			self.notify_event("timelapse_done", payload)
			return True
		if event == "MovieFailed":
			self.notify_event("timelapse_failed", payload)
			return True
		
		self._logger.debug("Event {} was not handled".format(event))	
		return True

	def on_print_progress(self, location, path, progress):
		self.notify_event("transfer_progress" if self.uploading else "printing_progress",{"progress": progress})


	def notify_event(self,eventID,data={}):
		if(eventID not in self.events):
			self._logger.error("Tried to notifiy on inexistant eventID : ", eventID)
			return False
		
		tmpConfig = self._settings.get(["events", eventID],merged=True)
		moviePath = None
		
		if tmpConfig["enabled"] != True:
			self._logger.debug("Event {} is not enabled. Returning gracefully".format(eventID))
			return False

		if eventID == "timelapse_done" and tmpConfig["upload_movie"] == True:
			moviePath = data["movie"]

		# Setup default values
		data.setdefault("progress",0)
		data.setdefault("remaining",0)
		data.setdefault("remaining_formatted","0s")
		data.setdefault("spent",0)
		data.setdefault("spent_formatted","0s")

		# Special case for progress eventID : we check for progress and steps
		if eventID == 'printing_progress' and (\
			int(tmpConfig["step"]) == 0 \
			or int(data["progress"]) == 0 \
			or int(data["progress"]) % int(tmpConfig["step"]) != 0 \
			or (int(data["progress"]) == 100) \
		) :
			return False			

		tmpDataFromPrinter = self._printer.get_current_data()
		if tmpDataFromPrinter["progress"] is not None and tmpDataFromPrinter["progress"]["printTimeLeft"] is not None:
			data["remaining"] = int(tmpDataFromPrinter["progress"]["printTimeLeft"])
			data["remaining_formatted"] = str(datetime.timedelta(seconds=data["remaining"]))
		if tmpDataFromPrinter["progress"] is not None and tmpDataFromPrinter["progress"]["printTime"] is not None:
			data["spent"] = int(tmpDataFromPrinter["progress"]["printTime"])
			data["spent_formatted"] = str(datetime.timedelta(seconds=data["spent"]))

		self._logger.debug("Available variables for event " + eventID +": " + ", ".join(list(data)))
		try:
			message = tmpConfig["message"].format(**data)
		except KeyError as error:
			message = tmpConfig["message"] + \
				"""\r\n:sos: **OctoRant Warning**""" + \
				"""\r\n The variable `{""" +  error.args[0] +"""}` is invalid for this message: """ + \
				"""\r\n Available variables: `{""" + '}`, `{'.join(list(data)) +"}`"
		finally:
			return self.send_message(eventID, message, tmpConfig["with_snapshot"] if "with_snapshot" in tmpConfig else None, moviePath)

	def exec_script(self, eventName, which=""):

		# I want to be sure that the scripts are allowed by the special configuration flag
		scripts_allowed = self._settings.get(["allow_scripts"],merged=True)
		if scripts_allowed is None or scripts_allowed == False:
			return ""

		# Finding which one should be used.
		script_to_exec = None
		if which == "before":
			script_to_exec = self._settings.get(["script_before"], merged=True)
		
		elif which == "after":
			script_to_exec = self._settings.get(["script_after"], merged=True)
		
		# Finally exec the script
		out = ""
		self._logger.debug("{}:{} File to start: '{}'".format(eventName, which, script_to_exec))

		try:
			if script_to_exec is not None and len(script_to_exec) > 0 and os.path.exists(script_to_exec):
				out = subprocess.check_output(script_to_exec)
		except (OSError, subprocess.CalledProcessError) as err:
				out = err
		finally:
			self._logger.debug("{}:{} > Output: '{}'".format(eventName, which, out))
			return out


	def send_message(self, eventID, message, withSnapshot=False, withMovie=None):

		# return false if no URL is provided
		if "http" not in self._settings.get(["url"],merged=True):
			return False

		# exec "before" script if any
		self.exec_script(eventID, "before")
		
		# Get snapshot if asked for
		snapshot = None
		media = Media(self._settings, self._logger)

		if withSnapshot:
			snapshot = media.grab_snapshot()
		
		if not withMovie == None:
			snapshot = media.grab_file(withMovie)
			
		# Send to Discord WebHook
		discordMsg = DiscordMessage(
			self._settings.get(["url"], merged=True),
			message,
			self._settings.get(["username"],merged=True),
			self._settings.get(['avatar'],merged=True),
			snapshot 
		)		

		discordMsg.start()

		# exec "after" script if any
		self.exec_script(eventID, "after")

		return True
		
# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "OctoRant"
__plugin_pythoncompat__ = ">=2.7,<4"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = OctorantPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

