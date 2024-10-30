#!/bin/bash

# Check id debug argument or help argument is passed
if [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    echo "Usage: ./start.sh [debug]"
    echo "Starts the Pupil Capture and Pupil Message Parser"
    echo "Optional argument: debug"
    echo "  debug: Starts the Pupil Capture in debug mode"
    exit 0
fi



echo "Starting Pupil Capture"
python3 /app/pupil/pupil_src/main.py capture --hide-ui &
PUPIL_PID=$!


if [ "$1" == "debug" ]; then
    echo "Starting Pupil Capture in debug mode"
    python3 /pupil_module/message_parser.py --enable-logging &
    PARSER_PID=$!
else
    echo "Starting Pupil Capture"
    python3 /pupil_module/message_parser.py &
    PARSER_PID=$!
fi

# Wait for both processes
wait $PUPIL_PID $PARSER_PID