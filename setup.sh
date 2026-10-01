#!/bin/bash
set -e

# Clone 5etools source data
if [ ! -d "5etools-src" ]; then
  git clone --depth 1 https://github.com/5etools-mirror-3/5etools-src/
fi

# Install dependencies
pip install ebooklib requests
# optional for conversion of the .webp pictures to .jpg for older e-readers:
# pip install Pillow

# Download D&D headline fonts (Nodesto Caps Condensed)
if [ ! -f "NodestoCapsCondensed.otf" ]; then
  curl -L -o "NodestoCapsCondensed.otf" \
    "https://raw.githubusercontent.com/jonathonf/solbera-dnd-fonts/master/Nodesto%20Caps%20Condensed/Nodesto%20Caps%20Condensed.otf"
fi

if [ ! -f "NodestoCapsCondensed-Bold.otf" ]; then
  curl -L -o "NodestoCapsCondensed-Bold.otf" \
    "https://raw.githubusercontent.com/jonathonf/solbera-dnd-fonts/master/Nodesto%20Caps%20Condensed/Nodesto%20Caps%20Condensed-Bold.otf"
fi