import logging
from flumine import BaseStrategy
from flumine.order.trade import Trade
from flumine.order.order import OrderStatus
from flumine.order.ordertype import LimitOrder
from datetime import timedelta
from collections import OrderedDict

from math import floor

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


class MarketMakingStrategy(BaseStrategy):
    def __init__(self, *args, min_spread_ticks=2, price_adjustment_ticks=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_trade = None
        self.price_increments = [
            (1.01, 2, 0.01), (2, 3, 0.02), (3, 4, 0.05), (4, 6, 0.1),
            (6, 10, 0.2), (10, 20, 0.5), (20, 30, 1), (30, 50, 2),
            (50, 100, 5), (100, 1000, 10)
        ]
        self.min_spread_ticks = min_spread_ticks
        self.price_adjustment_ticks = price_adjustment_ticks
        self.active_trades = {}
        self.stake_size = 0.1

    def check_market_book(self, market, market_book):
        if market.market_type not in ["WIN", "PLACE"]:
            return False
        market_start_time = market_book.market_definition.market_time
        time_to_start = market_start_time - market_book.publish_time
        return time_to_start >= timedelta(seconds=30) and not market.closed

    def get_tick_size(self, price):
        for low, high, increment in self.price_increments:
            if low <= price < high:
                return increment
        return 0.01

    def get_next_tick(self, price):
        tick_size = self.get_tick_size(price)
        return round(price + tick_size, 2)

    def calculate_new_price(self, current_price, best_opposite_price, side):
        if side == "BACK":
            new_price = min(current_price, self.get_price_ticks_away(
                best_opposite_price, -self.price_adjustment_ticks))
        else:  # LAY
            new_price = max(current_price, self.get_price_ticks_away(
                best_opposite_price, self.price_adjustment_ticks))
        return new_price

    def get_price_ticks_away(self, price, ticks):
        for _ in range(abs(ticks)):
            if ticks > 0:
                price = self.get_next_tick(price)
            else:
                price = self.get_previous_tick(price)
        return price

    def get_previous_tick(self, price):
        for low, high, increment in reversed(self.price_increments):
            if low < price <= high:
                return round(price - increment, 2)
        # Default to smallest decrement if price is out of range
        return round(price - 0.01, 2)

    def calculate_spread_in_ticks(self, best_back, best_lay):
        if best_back is None or best_lay is None:
            return 0
        tick_size = self.get_tick_size(best_lay)
        return floor((best_lay / tick_size) - (best_back / tick_size))

    def get_best_price(self, prices):
        return prices[0]['price'] if prices else None

    def process_market_book(self, market, market_book):
        if market_book is None or market_book.runners is None:
            logging.warning(
                f"Invalid market book for market {market.market_id}")
            return

        for runner in market_book.runners:
            if runner is None:
                continue

            selection_id = runner.selection_id
            best_back = self.get_best_price(runner.ex.available_to_back)
            best_lay = self.get_best_price(runner.ex.available_to_lay)

            if best_back is None or best_lay is None:
                logging.info(
                    f"No prices available for {selection_id}, skipping")
                continue

            spread_ticks = self.calculate_spread_in_ticks(best_back, best_lay)
            logging.info(
                f"Spread for {selection_id}: {spread_ticks} ticks {best_back} {best_lay}")

            if selection_id in self.active_trades:
                self.update_existing_order(
                    market, market_book, runner, best_back, best_lay)
            elif spread_ticks >= self.min_spread_ticks:
                print(f'backing at {best_lay} {best_back}')
                self.place_back_order(
                    market, market_book, runner, best_lay - self.get_tick_size(best_lay))
                break  # Only place one new order at a time

    def update_existing_order(self, market, market_book, runner, best_back, best_lay):
        selection_id = runner.selection_id
        active_trade = self.active_trades[selection_id]

        if active_trade["back"] and active_trade["back"].status == OrderStatus.EXECUTABLE:
            current_back_price = active_trade["back"].order_type.price
            new_back_price = self.calculate_new_price(
                current_back_price, best_lay, "BACK")

            if new_back_price != current_back_price:
                print(
                    f'updating back order price {best_back} {best_lay} to be {new_back_price}')
                self.update_order_price(
                    market, active_trade["back"], new_back_price)

        elif active_trade["lay"] and active_trade["lay"].status == OrderStatus.EXECUTABLE:
            current_lay_price = active_trade["lay"].order_type.price
            print(
                f'updating lay order price {best_back} {best_lay} to be {best_back}')
            new_lay_price = self.calculate_new_price(
                current_lay_price, best_back, "LAY")

            if new_lay_price != current_lay_price:
                self.update_order_price(
                    market, active_trade["lay"], new_lay_price)

    def update_order_price(self, market, order, new_price):
        old_price = order.order_type.price
        order.order_type.price = new_price
        market.update_order(order, new_price)
        logging.info(
            f"Updated {order.side} order for {order.selection_id} from {old_price} to {new_price}")

    def process_orders(self, market, orders):
        for order in orders:
            if order.status == OrderStatus.EXECUTION_COMPLETE:
                selection_id = order.selection_id
                print(
                    f"Order {order.id} for {selection_id} executed at {order.average_price_matched}")

                if selection_id in self.active_trades:
                    active_trade = self.active_trades[selection_id]
                    back_id = active_trade["back"].id if active_trade["back"] else None
                    lay_id = active_trade["lay"].id if active_trade["lay"] else None

                    if order.id in [back_id, lay_id]:
                        if order.side == "BACK":
                            runner = next(
                                (r for r in market.market_book.runners if r.selection_id == selection_id), None)
                            if runner:
                                best_back = self.get_best_price(
                                    runner.ex.available_to_back)
                                if best_back:
                                    next_back_price = best_back + \
                                        self.get_tick_size(self.get_best_price(
                                            runner.ex.available_to_lay))
                                    self.place_lay_order(
                                        market, market.market_book, runner, next_back_price)
                                else:
                                    logging.info(
                                        f"No back prices available for {selection_id}, cancelling trade")
                                    del self.active_trades[selection_id]
                            else:
                                logging.warning(
                                    f"Runner {selection_id} not found in market book")
                                del self.active_trades[selection_id]
                        elif order.side == "LAY":
                            # Both back and lay orders are complete, remove active trade
                            logging.info(
                                f"Completed full trade for {selection_id}")
                            del self.active_trades[selection_id]
                    else:
                        logging.warning(
                            f"Executed order {order.id} doesn't match active trade orders")
                else:
                    logging.warning(
                        f"Executed order {order.id} received but no active trade")

    def place_back_order(self, market, market_book, runner, price):
        selection_id = runner.selection_id
        if selection_id in self.active_trades:
            logging.info(
                f"Active trade already exists for {selection_id}, skipping back order")
            return

        trade = Trade(market_book.market_id,
                      selection_id, runner.handicap, self)
        order = trade.create_order(
            side="BACK",
            order_type=LimitOrder(price, self.stake_size, "LIMIT"),
            notes=OrderedDict(),
        )
        market.place_order(order)
        self.active_trades[selection_id] = {"back": order, "lay": None}
        logging.info(f"Placed back order for {selection_id} at {price}")

    def place_lay_order(self, market, market_book, runner, price):
        selection_id = runner.selection_id
        if selection_id not in self.active_trades or self.active_trades[selection_id]["lay"]:
            logging.info(
                f"No active trade or lay order already exists for {selection_id}, skipping lay order")
            return

        trade = Trade(market_book.market_id,
                      selection_id, runner.handicap, self)
        order = trade.create_order(
            side="LAY",
            order_type=LimitOrder(price, self.stake_size, "LIMIT"),
            notes=OrderedDict(),
        )
        market.place_order(order)
        self.active_trades[selection_id]["lay"] = order
        logging.info(f"Placed lay order for {selection_id} at {price}")
