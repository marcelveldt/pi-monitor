#!/bin/bash
set -e

THIS_FILE=$(readlink -f "$0")
THIS_DIR=$(dirname "$THIS_FILE")
INSTALL_DIR="~/spotify-web-chroot"
INSTALL_COMPLETE="$INSTALL_DIR/.install_complete"

if [ ! -f "$INSTALL_COMPLETE" ]; then
        # perform download of the chroot version of spotify-connect-web for amv6 compatability
        mkdir -p $INSTALL_DIR
        cd $INSTALL_DIR
        curl -L https://github.com/Fornoth/spotify-connect-web/releases/download/0.0.4-alpha/spotify-connect-web_0.0.4-alpha_chroot.tar.gz | sudo tar xz
        touch $INSTALL_COMPLETE
fi

# run executable from chroot
trap "sudo umount $INSTALL_DIR/dev $INSTALL_DIR/proc" EXIT
sudo mount --bind /dev $INSTALL_DIR/dev
sudo mount -t proc proc $INSTALL_DIR/proc/
sudo cp /etc/resolv.conf $INSTALL_DIR/etc/
sudo cp $THIS_DIR/spotify_appkey.key $INSTALL_DIR/usr/src/app
sudo chroot $INSTALL_DIR /bin/bash -c "cd /usr/src/app && python main.py $*"
