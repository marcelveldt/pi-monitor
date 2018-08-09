#!/bin/bash

if grep -Fxq '"AUTO_UPDATE_ON_STARTUP": true' /etc/pi-monitor.json
then
    echo "Auto updating to latest version..."
    git fetch --all
    git reset --hard origin/master
else
    echo "Auto update is disabled"
fi

# run the python code
python main.py
exit $?