"""Regresión del cierre operativo al vencer el horizonte."""
import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import backtesting


class CloseWindowTests(unittest.TestCase):
    def test_late_evaluation_excludes_later_prices(self):
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        start = stamp.timestamp()*1000
        def bar(hours, price):
            return [start+(hours-1)*3600000, 0, 0, 0, price, 0, start+hours*3600000-1]
        pred = dict(created_at=stamp.isoformat(), horizon_days=7, alpha_id="X", price_at_prediction=100)
        with patch.object(backtesting.binance_alpha, "get_klines",
                          return_value=[bar(167,100),bar(168,102),bar(169,1000)]) as fetch:
            result = backtesting.evaluate_prediction(pred)
            self.assertEqual(result["price_after"], 102)
            self.assertEqual(result["max_return_pct"], 2)
            self.assertIsNone(result["return_at_day1_pct"])
            self.assertEqual(fetch.call_args.kwargs["end_time_ms"], start+168*3600000)
        with patch.object(backtesting.binance_alpha, "get_klines", return_value=[bar(148,102)]):
            self.assertIsNone(backtesting.evaluate_prediction(pred))
