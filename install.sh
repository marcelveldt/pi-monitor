#!/bin/bash
set -e


install_service () {
cat > /etc/systemd/system/pi-monitor.service <<'EOF'
[Unit]
Description=GPIO Monitoring
After=multi-user.target

[Service]
Type=idle
ExecStart=python $s/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod +x /etc/systemd/system/pi-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable pi-monitor.service 

sudo systemctl start pi-monitor.service 
}

if [ -d "/mnt/dietpi_userdata/" ]; then
    ROOT_DIR="/mnt/dietpi_userdata/"
    IS_DIETPI="1"
    echo "detected Dietpi"
else
    ROOT_DIR="~/"
    IS_DIETPI="0"
    echo "DietPi not detected, assuming some other Debian based distro"
fi

INSTALL_DIR="$ROOT_DIRpi-monitor"


if [ ! -d "$INSTALL_DIR" ]; then
        echo "Installing Pi Monitor to $INSTALL_DIR"
        if [ "$IS_DIETPI" == "1" ]; then
            dietpi-software install 16
            dietpi-software install 17
            dietpi-software install 130
        else
            apt-get install -y build-essential git python-pip
        fi
        cd $ROOT_DIR
        git clone https://github.com/marcelveldt/pi-monitor
        install_service $INSTALL_DIR
        
else
    # just update the existing install
    sudo systemctl stop pi-monitor.service 
    echo "Updating Pi Monitor in $INSTALL_DIR"
    cd $INSTALL_DIR
    git pull
    sudo systemctl start pi-monitor.service 
fi

