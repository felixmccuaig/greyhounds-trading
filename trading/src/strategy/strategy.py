import logging

from flumine import BaseStrategy

from datetime import timedelta

import numpy as np

from collections import deque

from src.trade.TradeWithStopLoss import TradeWithStopLoss, TradeSide, StopLossType


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
)


class MovingAverageStrategy(BaseStrategy):
    def __init__(self, *args, short_window=10, long_window=30, stake_size=2,
                 stop_loss=0.05, take_profit=0.30, trailing_stop_loss=True, min_volume=1, max_liability=5, price_threshold=0.01, **kwargs):
        super().__init__(*args, **kwargs)
        self.short_window = short_window
        self.long_window = long_window
        self.stake_size = stake_size
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.trailing_stop_loss = trailing_stop_loss
        self.price_threshold = price_threshold
        self.min_volume = min_volume
        self.max_liability = max_liability
        self.prices = {}
        self.short_ma = {}
        self.long_ma = {}
        self.trades = {}

    def calculate_proportional_stake(self, odds, max_liability):
        if odds <= 1:
            return 0
        stake = max_liability / (odds - 1)
        return max(round(stake, 2), 0.05)

    def check_market_book(self, market, market_book):
        if market.market_type != "WIN" and market.market_type != "PLACE":
            return False

        market_start_time = market_book.market_definition.market_time
        time_to_start = market_start_time - market_book.publish_time

        if time_to_start <= timedelta(minutes=0) and not market.closed:
            for key, value in self.trades.items():
                order = value.exit_position("Going in play")
                if order is not None:
                    market.place_order(order)
            return False

        if time_to_start <= timedelta(minutes=5) and not market.closed:
            return True
        return False

    def process_market_book(self, market, market_book):
        if market_book is None or market_book.runners is None:
            logging.warning(
                f"Invalid market book for market {market.market_id}")
            return

        for runner in market_book.runners:
            if runner is None:
                continue

            selection_id = runner.selection_id
            ltp = runner.last_price_traded
            total_matched = runner.total_matched

            batb = None
            batl = None

            if len(runner.ex.available_to_back):
                batb = runner.ex.available_to_back[0]

            if len(runner.ex.available_to_lay):
                batl = runner.ex.available_to_lay[0]

            if ltp is None or total_matched is None:
                continue

            if selection_id in self.trades:
                if self.trades[selection_id].is_closed():
                    del self.trades[selection_id]
                    continue

                order = self.trades[selection_id].update_price(
                    ltp, ltp, ltp, market_book.publish_time)

                if order is not None:
                    logging.info(
                        f"Placed exit position order. {order.notes['trigger']}")
                    market.place_order(order)

            # Initialize price history if not already present
            if selection_id not in self.prices:
                self.prices[selection_id] = deque(maxlen=self.long_window)

            # Update price history
            self.prices[selection_id].append(ltp)

            # Calculate moving averages
            prices_list = list(self.prices[selection_id])
            if len(prices_list) < self.long_window:
                continue

            self.short_ma[selection_id] = np.mean(
                prices_list[-self.short_window:])
            self.long_ma[selection_id] = np.mean(prices_list)

            # We think the price will decrease
            if self.short_ma[selection_id] > self.long_ma[selection_id] and ltp >= self.short_ma[selection_id]:
                trade_side = TradeSide.SHORT
            # We think the price will increase
            elif self.short_ma[selection_id] < self.long_ma[selection_id] and ltp <= self.short_ma[selection_id]:
                trade_side = TradeSide.LONG
            else:
                continue

            # Enter a new trade.
            if selection_id not in self.trades:
                trade = TradeWithStopLoss(
                    market_id=market.market_id,
                    selection_id=runner.selection_id,
                    handicap=runner.handicap,
                    strategy=self,
                    side=trade_side,
                    stop_loss_type=StopLossType.TRAILING,
                    trailing_stop_distance=0.5
                )
                trade.update_price(ltp, ltp, ltp, market_book.publish_time)
                self.trades[selection_id] = trade
                market.place_order(
                    self.trades[selection_id].enter_position(self.stake_size))

    def process_orders(self, market, orders) -> None:
        for key, value in self.trades.items():
            value.update_orders(orders)
