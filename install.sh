#!/bin/bash
set -e


if [ -d "/mnt/dietpi_userdata" ]; then
    ROOT_DIR="/mnt/dietpi_userdata"
else
    ROOT_DIR="~"
fi

INSTALL_DIR="$ROOT_DIR/pi-monitor"
echo "Installing Pi Monitor to $INSTALL_DIR"


if [ ! -d "$INSTALL_DIR" ]; then
        apt-get install -y build-essential git
        cd $ROOT_DIR
        git clone https://github.com/marcelveldt/pi-monitor
else
    # just update the existing install
    cd $INSTALL_DIR
    git pull
fi