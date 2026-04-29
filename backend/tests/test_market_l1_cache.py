import unittest
from unittest.mock import AsyncMock, patch
import backend.market_l1_cache as market_l1_cache

class TestMarketL1Cache(unittest.IsolatedAsyncioTestCase):

    @patch('backend.market_l1_cache.logger.warning')
    @patch('backend.connectors.macro.MacroHealthConnector.fetch_data', new_callable=AsyncMock)
    async def test_refresh_exception_handling(self, mock_fetch_data, mock_logger_warning):
        # Arrange
        exception_msg = "Simulated network failure"
        mock_fetch_data.side_effect = Exception(exception_msg)

        # Act
        await market_l1_cache.refresh()

        # Assert
        mock_fetch_data.assert_called_once()
        mock_logger_warning.assert_called_once()

        args, _ = mock_logger_warning.call_args
        self.assertEqual(args[0], "[MarketL1] refresh failed: %s")
        self.assertIsInstance(args[1], Exception)
        self.assertEqual(str(args[1]), exception_msg)

if __name__ == '__main__':
    unittest.main()
