#!/bin/bash

source /ros_entrypoint.sh

# Loop ros topic list with 3 sec delay
while true; do
    rostopic list
    sleep 3
done