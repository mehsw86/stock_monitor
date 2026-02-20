#!/bin/bash

# .env 파일이 있으면 로드
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

python3 stock_monitor.py
