#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] && { set -a; source .env; set +a; }
export PYTHONPATH="$PWD/src"
streamlit run app.py --server.port 8501 --server.headless true
