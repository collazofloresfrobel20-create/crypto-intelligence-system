import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import portfolio_prices as portfolio


class PortfolioPriceTests(unittest.TestCase):
    def test_only_finished_valid_candles_survive(self):
        hour = 3600000
        valid = [hour, 10, 12, 9, 11, 1, 2*hour-1]
        malformed = [hour, 10, 8, 9, 11, 1, 2*hour-1]
        future = [2*hour, 11, 12, 9, 10, 1, 3*hour-1]
        missing = [4, "x"]
        self.assertEqual(portfolio.valid_bars([valid,valid,malformed,future,missing],hour+1,2*hour),
                         [[hour,10,12,9,11,2*hour-1]])

    def test_delisted_market_is_missing_not_a_zero_or_finished_path(self):
        record = {"alpha_id":"ALPHA_1", "created_at":"2026-09-01T00:00:00+00:00"}
        with patch.object(portfolio.binance_alpha,"get_klines",return_value=[]) as read:
            self.assertEqual(portfolio.get_path(record,datetime(2026,11,1,tzinfo=timezone.utc)),([],0,"sin_velas"))
            self.assertEqual(read.call_args.kwargs["end_time_ms"],
                             int(datetime(2026,10,1,2,tzinfo=timezone.utc).timestamp()*1000))


if __name__ == "__main__":
    unittest.main()
