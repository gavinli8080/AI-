#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
mkdir -p data
if [ ! -f .env ]; then cp .env.example .env; fi
echo "部署完成。先编辑 .env，再执行: source .venv/bin/activate && python main.py --once"
