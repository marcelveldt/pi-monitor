#!/bin/bash
set -e

THIS_FILE=$(readlink -f "$0")
THIS_DIR=$(dirname "$THIS_FILE")

if [ -d "/mnt/dietpi_userdata/" ]; then
    ROOT_DIR="/mnt/dietpi_userdata/"
    IS_DIETPI=1
    echo "detected Dietpi"
else
    ROOT_DIR="/root/"
    IS_DIETPI=0
    echo "DietPi not detected, assuming some other Debian based distro"
fi

APP_NAME="spotify-web-chroot"
INSTALL_DIR="$ROOT_DIR$APP_NAME"
INSTALL_COMPLETE="$INSTALL_DIR/.install_complete"

if [ ! -f "$INSTALL_COMPLETE" ]; then
        # perform download of the chroot version of spotify-connect-web for amv6 compatability
        mkdir -p $INSTALL_DIR
        cd $INSTALL_DIR
        curl -L https://github.com/Fornoth/spotify-connect-web/releases/download/0.0.4-alpha/spotify-connect-web_0.0.4-alpha_chroot.tar.gz | sudo tar xz
        touch $INSTALL_COMPLETE
fi

# copy our customized code
cp -v "$THIS_DIR/." "$INSTALL_DIR/usr/src/app/"

# run executable from chroot
trap "sudo umount $INSTALL_DIR/dev $INSTALL_DIR/proc" EXIT
sudo mount --bind /dev $INSTALL_DIR/dev
sudo mount -t proc proc $INSTALL_DIR/proc/
sudo cp /etc/resolv.conf $INSTALL_DIR/etc/
sudo cp $THIS_DIR/spotify_appkey.key $INSTALL_DIR/usr/src/app
sudo chroot $INSTALL_DIR /bin/bash -c "cd /usr/src/app && python main.py $*"
