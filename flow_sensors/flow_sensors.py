# !/usr/bin/env python
#  This plugin includes example functions that are triggered by events in sip.py

# Note: RPi direct interface to sensors is not currently supported even though the option
#  is given in the settings page.  There's some skeleton code ready below where support can
#  be added if we decide it's important.

# Operation: at the lowest level, the flow sensor generates a series of pulses on an input
# pin of the Arduino or RPi that is related to the flow rate by a forumla given by the
# specs for the flow sensor.  This pulse is used to increment a software counter on
# the Arudino or RPi using an interrupt routine.

# This plugin creates a thread that runs every N seconds (e.g. 3 seconds) and that reads
# this counter and determines both the current flow rate (liters or gallons per hour)
# and the total amount of water flow (in liters or gallons) since the counter was reset.

# The flow rates and flow amounts for each station is stored in a gv.plugin_data['fs']
# dictionary.
import web  # web.py framework
import gv  # Get access to SIP's settings
from urls import urls  # Get access to ospi's URLs
from ospi import template_render  #  Needed for working with web.py templates
from webpages import ProtectedPage  # Needed for security
import json  # for working with data file
import time
import thread
import random
import serial
from blinker import signal

# Add new URLs to access classes in this plugin.
urls.extend([
    '/flow_sensors-sp', 'plugins.flow_sensors.settings',
    '/flow_sensors-save', 'plugins.flow_sensors.save_settings'
    ])

gv.plugin_menu.append(['Flow Sensors Plugin', '/flow_sensors-sp'])

#CONVERSION_MULTIPLIER = {'Seeed 1/2 inch': {'Liters': 60.0/7.5, 'Gallons': 60/7.5/3.78541},
#                         'Seeed 3/4 inch': {'Liters': 60.0/5.5, 'Gallons': 60/5.5/3.78541}}

def fixPerHour():  # recalculate settings derived from other settings
    isLiters = gv.plugin_data['fs']['settings']['units'] == 'Liters'
    gv.plugin_data['fs']['settings']['rate_units'] = 'LpH' if isLiters else 'GpH'


print("flow sensors plugin loaded...")
# initialize settings and other variables in gv
gv.plugin_data['fs'] = {}
gv.plugin_data['fs']['rates'] = [0]*8
gv.plugin_data['fs']['settings'] = {}
gv.plugin_data['fs']['settings']['interface'] = 'Simulated'
gv.plugin_data['fs']['settings']['sensor_type'] = 'Seeed/Digiten 1/2 inch'
gv.plugin_data['fs']['settings']['pulses_per_liter'] = 450.0
gv.plugin_data['fs']['settings']['units'] = 'Liters'
gv.plugin_data['fs']['settings']['rate_units'] = 'LpH'
fixPerHour()

print("Settings initialized to: " + str(gv.plugin_data['fs']['settings']))
try:
    with open('./data/flow_sensors.json', 'r') as f:  # Read settings from json file if it exists
        gv.plugin_data['fs']['settings'] = json.load(f)
        print("Updating settings from json file: " + str(gv.plugin_data['fs']['settings']))
except IOError:  # If file does not exist return empty value
    print("No flow_sensors.json file")
    print("my settings here are: " + str(gv.plugin_data['fs']['settings']))


# add this plugin's log value to the SIP log
try:
    gv.logged_values.append( [_('usage'), lambda : '{:.2f}'.format(gv.plugin_data["fs"]["program_amounts"][gv.lrun[0]]) ])
except AttributeError:
    print("gv.logged_values doesn't exist so logging not available for flow_sensor plugin")


# TODO: add support for other types of RPi serial interfaces with different /dev/names

def flow_sensor_loop():
    """
    This tread will update the flow sensor values every N seconds.
    """
    delta_t = 3.0 # seconds
    while True:
        update_flow_values()
        time.sleep(delta_t)

def reset_flow_sensors():
    """
    Resets parameters used by this plugin for all three flow_sensor types.
    Used at initialization and at the start of each Program/Run-Once 
    """
    print("resetting flow sensors")
    gv.plugin_data['fs']['start_time'] = time.time()
    gv.plugin_data['fs']['prev_read_time'] = time.time()
    gv.plugin_data['fs']['prev_read_cntrs'] = [0]*8
    gv.plugin_data['fs']['program_amounts'] = [0]*8

    if gv.plugin_data['fs']['settings']['interface'] == 'Simulated':
        gv.plugin_data['fs']['simulated_counters'] = [0]*8
        return True

    elif gv.plugin_data['fs']['settings']['interface'] == 'Arduino-Serial':
        serial_ch = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
        gv.plugin_data['fs']['serial_chan'] = serial_ch
        time.sleep(0.1)
        serial_ch.write("RS\n")
        serial_ch.flush()
        time.sleep(0.1)
        line = serial_ch.readline()
        print("values from Arduino on establishing serial port")
        print(line)
        return True

    elif gv.plugin_data['fs']['settings']['interface'] == 'RaspberryPi-GPIO':
        pass
        return True
    print("Flow Sensor Type Failed in Reset")
    return False

def read_flow_counters(reset=False):
    """
    Reads counters corresponding to each flow sensor.
    Supports simulated flow sensors (for testing UI), flow sensors connected to an Arduino and
      perhaps flow sensors connected directly to the Pi.
    """
    # print "reading flow sensors"
    if gv.plugin_data['fs']['settings']['interface'] == 'Simulated':
        if reset:
            gv.plugin_data['fs']['simulated_counters'] = [0]*8
        else:
            gv.plugin_data['fs']['simulated_counters'] = [cntr + random.random()*40 + 180 for
                                                          cntr in gv.plugin_data['fs']['simulated_counters']]
        return gv.plugin_data['fs']['simulated_counters']

    elif gv.plugin_data['fs']['settings']['interface'] == 'Arduino-Serial':
        serial_ch = gv.plugin_data['fs']['serial_chan']
        if reset:
            serial_ch.write('RS\n')
        else:
            serial_ch.write('RD\n')
        serial_ch.flush()
        #print("Writing to Arduino")
        time.sleep(0.2)
        line = serial_ch.readline().rstrip()
        #print("serial input from Arduino is: " + line)
        #print("serial input has been printed")
        if line == '':
            return [0]*8
        else:
            vals = map(int, line.split(','))
        return vals

    elif gv.plugin_data['fs']['settings']['interface'] == 'RaspberryPi-GPIO':
        pass
        return [0]*8

    print("Flow Sensor Type Failed in Read")
    return False

def update_flow_values():
    """
    Updates gv values for the current flow rate and accumulated flow amount for each flow sensors.
    """
    pulses_per_liter = gv.plugin_data['fs']['settings']['pulses_per_liter']
    units = gv.plugin_data['fs']['settings']['units']
    current_time = time.time()

    elapsed_prev_read = current_time - gv.plugin_data['fs']['prev_read_time']  # for flow rate
    # print("elapsed time: " + str(elapsed_time))

    prev_cntrs = gv.plugin_data['fs']['prev_read_cntrs']

    curr_cntrs = read_flow_counters()

    # calculate flow amount in Liters as # of pulses * liters_per_pulse
    #  or #_of_pulses / (pulses_per_liter)

    amt_conv_mult = 1.0/pulses_per_liter  # or liters per pulse
    if 'units' == 'Gallons':
        amt_conv_mult /= 3.78541

    gv.plugin_data['fs']['program_amounts'] = [cntr*amt_conv_mult for cntr in curr_cntrs]

    # calculate flow rate in Liters per hour = pulses_per_second * (seconds_per_hour * liters_per_pulse)
    rate_conv_mult = 60.*60./pulses_per_liter
    if 'units' == 'Gallons':
        rate_conv_mult /= 3.78541

    gv.plugin_data['fs']['rates'] = [(cntr-prev_cntr)*rate_conv_mult/elapsed_prev_read for \
                                     cntr, prev_cntr in zip(curr_cntrs, prev_cntrs)]
    
    # print("Rates:" + str(gv.plugin_data['fs']['rates']))
    # print("Amounts:" + str(gv.plugin_data['fs']['program_amounts']))

    gv.plugin_data['fs']['prev_read_time'] = current_time
    gv.plugin_data['fs']['prev_read_cntrs'] = curr_cntrs
   
### Stations where sheduled to run ###
# gets triggered when:
#       - A program is run (Scheduled or "run now")
#       - Stations are manually started with RunOnce
def notify_station_scheduled(name, **kw):
    """
    Subscribes to the stations_scheduled signal and used to reset the flow_sensor counters
      and flow rate/amount values in the gv.
    """
    reset_flow_sensors()
    #print("Some stations have been scheduled: {}".format(str(gv.rs)))

reset_flow_sensors()
thread.start_new_thread(flow_sensor_loop, ())

program_started = signal('stations_scheduled') # subscribe to signal when programs/stations start running
program_started.connect(notify_station_scheduled) # specify callback for this signal

 #############################################
 # Web Interface for plugin settings
 #############################################
class settings(ProtectedPage):
    """
    Load an html page for entering plugin settings.
    """
    
    def GET(self):
        settings = gv.plugin_data['fs']['settings']
        print("settings are:  " + str(settings))
        print ("GET method in settings class")
        try:
            with open('./data/flow_sensors.json', 'r') as f:  # Read settings from json file if it exists
                settings = json.load(f)
                reset_flow_sensors()
        except IOError:  # If file does not exist return empty value
            print("No flow_sensors.json file")
            print "my settings here are: " + str(settings)
        return template_render.flow_sensors(settings)  # open settings page

class save_settings(ProtectedPage):
    """
    Save user input to json file.
    Will create or update file when SUBMIT button is clicked
    CheckBoxes only appear in qdict if they are checked.
    """
    def GET(self):
        settings = gv.plugin_data['fs']['settings']
        qdict = web.input()  # Dictionary of values returned as query string from settings page.
        print "qdict = " + str(qdict)  # for testing
        print "settings : " + str(settings)
        for key in qdict:
            # watch out for checkboxes since they only return a value in qdict if they're checked!!
            if key == "pulses_per_liter":
                settings[key] = float(qdict[key])
            else:
                settings[key] = qdict[key]
        fixPerHour()
        reset_flow_sensors()
        print "after update from qdict, settings = " + str(settings)
        with open('./data/flow_sensors.json', 'w') as f:  # Edit: change name of json file
             json.dump(settings, f) # save to file
             print "flow sensor settings file saved"          
        raise web.seeother('/')  # Return user to home page.

