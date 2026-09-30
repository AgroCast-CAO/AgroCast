"""Test setup: offline synthetic weather + a throw-away SQLite file (set BEFORE app import)."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["AGROCAST_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["AGROCAST_WEATHER_MODE"] = "synthetic"
