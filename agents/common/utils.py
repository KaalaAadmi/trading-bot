import json

# import yfinance as yf
import logging
from datetime import datetime, time
from decimal import Decimal

import holidays

logger = logging.getLogger("agents.common.utils")


def is_public_holiday():
    us_holidays = holidays.IN()  # Use IN() for India, or combine both if needed --> ISO codes are used for country
    today = datetime.now().date()
    return today in us_holidays


def is_weekend_or_holiday():
    today = datetime.now().date()
    return today.weekday() >= 5 or is_public_holiday()


def is_market_open():
    now = datetime.now().time()
    market_open = time(9, 15)  # Example: 9:15 AM
    market_close = time(16, 15)  # Example: 4:15 PM
    return market_open <= now <= market_close


def serialize(obj):
    if isinstance(obj, list):
        return json.dumps([serialize(i) for i in obj])
    elif isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    elif hasattr(obj, "isoformat"):  # Handles pandas.Timestamp, datetime, etc.
        return obj.isoformat()
    elif obj is None:
        return "null"
    else:
        return obj
