#!/bin/bash

#set -e

if [ -d "/mnt/dietpi_userdata/" ]; then
    ROOT_DIR="/mnt/dietpi_userdata/"
    IS_DIETPI=1
    echo "detected Dietpi"
else
    ROOT_DIR="/root/"
    IS_DIETPI=0
    echo "DietPi not detected, assuming some other Debian based distro"
fi

APP_NAME="pi-monitor"
INSTALL_DIR="$ROOT_DIR$APP_NAME"


install_service () {
cat > /etc/systemd/system/pi-monitor.service <<EOF
[Unit]
Description=$APP_NAME
After=multi-user.target

[Service]
Type=idle
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/run.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod +x /etc/systemd/system/$APP_NAME.service
sudo systemctl daemon-reload
sudo systemctl enable $APP_NAME.service 
sudo systemctl start $APP_NAME.service 
}


if [ ! -d "$INSTALL_DIR" ]; then
    echo "#########################################################################################################"
    echo " "
    echo " "
    echo "Installing $APP_NAME to $INSTALL_DIR"
    echo "Some required packages will be automatically installed, please be patient."
    read -p "Press enter to continue..."
    if [ "$IS_DIETPI" -eq 1 ]; then
        /DietPi/dietpi/dietpi-software install 16
        /DietPi/dietpi/dietpi-software install 17
        /DietPi/dietpi/dietpi-software install 130
    else
        apt-get install -y build-essential git python python-pip
    fi
    cd $ROOT_DIR
    git clone https://github.com/marcelveldt/pi-monitor
    install_service
    echo "#########################################################################################################"
    echo " "
    echo " "
    echo "Install Complete! "
    echo "Access the $APP_NAME on http://localhost or http://ip-of-this-pi"
    echo "Please note: at first launch the application will check several dependencies and perform some checks"
    echo "On slower systems it can take up to 10 minutes before the webservice is ready."
    echo " "
    echo " "
    echo "#########################################################################################################" 
else
    # just update the existing install
    echo "Updating $APP_NAME in $INSTALL_DIR"
    sudo systemctl stop $APP_NAME.service 
    cd $INSTALL_DIR
    git fetch --all
    git reset --hard origin/master
    install_service
fi
