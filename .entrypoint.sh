#!/bin/bash
Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset > /dev/null 2>&1 &
export DISPLAY=:99

exec "$@"