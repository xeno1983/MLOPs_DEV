#!/usr/bin/env bash
curl -fsSL http://archive.ubuntu.com/ubuntu/pool/main/t/tiff/ | grep -oE 'href="libtiff5_[^"]+_amd64\.deb"' | sed 's/href="//;s/"$//' | sort -u | tail -30
