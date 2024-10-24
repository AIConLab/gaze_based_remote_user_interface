#!/bin/bash

echo "Starting Pupil Capture"
python3 /app/pupil/pupil_src/main.py capture --hide-ui &
PUPIL_PID=$!

echo "Starting Pupil Message Parser"
python3 /pupil_module/message_parser.py &
PARSER_PID=$!

# Wait for both processes
wait $PUPIL_PID $PARSER_PID