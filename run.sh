#!/bin/bash

AUTO_UPDATE=$( sed -n 's/.*"AUTO_UPDATE_ON_STARTUP": \(.*\),/\1/p' /etc/pi-monitor.json )

if [[ $AUTO_UPDATE == "true" ]]
then
    echo "Auto updating to latest version..."
    git fetch --all
    git reset --hard origin/master
    pip list --outdated --format=freeze | grep -v '^\-e' | cut -d = -f 1  | xargs -n1 pip install -U
else
    echo "Auto update is disabled"
fi

# run the python code
python main.py
exit $?