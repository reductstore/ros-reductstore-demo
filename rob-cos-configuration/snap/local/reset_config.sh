#!/bin/sh -e

STORED_DEVICE_ID=$(cat $SNAP_COMMON/configuration/uid)
STORED_COS_SERVER_URL=$(cat $SNAP_COMMON/configuration/rob-cos-base-url)

cp -R $SNAP/etc/configuration/ $SNAP_COMMON/

snapctl set device-uid=$STORED_DEVICE_ID
snapctl set rob-cos-base-url=$STORED_COS_SERVER_URL
# must be in the SNAP root directory for the find command
cd $SNAP
bash $SNAP/usr/bin/search_and_replace.sh
