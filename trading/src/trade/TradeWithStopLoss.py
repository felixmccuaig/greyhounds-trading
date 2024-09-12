from flumine.order.order import BetfairOrder, LimitOrder, OrderStatus
from flumine.order.trade import Trade

import logging
from enum import Enum
from collections import OrderedDict

from datetime import timedelta

from src.utils.utils import position_if_lose, position_if_win


logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(message)s",
)


class TradeStatus(Enum):
    PENDING = "Pending"
    LIVE = "Live"
    COMPLETE = "Complete"


class StopLossType(Enum):
    FIXED = "Fixed"
    TRAILING = "Trailing"


class TradeSide(Enum):
    LONG = "long"
    SHORT = "short"


class TradeWithStopLoss(Trade):
    def __init__(self, market_id, selection_id, handicap, strategy, side, notes=None,
                 stop_loss_price=None, stop_loss_type=StopLossType.FIXED,
                 trailing_stop_distance=None, take_profit_percent=0.03):
        super().__init__(market_id, selection_id, handicap, strategy, notes)
        self.side = side
        self.stop_loss_price = stop_loss_price
        self.stop_loss_type = stop_loss_type
        self.trailing_stop_distance = trailing_stop_distance
        self.take_profit_percent = take_profit_percent
        self.best_back_price = None
        self.best_lay_price = None
        self.is_active = True
        self.open_order = None
        self.enter_price = None
        self.take_profit_price = None
        self.exit = None
        self.order_placed_time = None
        self.last_publish_time = None

        self.ltp = None

        self.max_price = None
        self.min_price = None

    def update_price(self, current_price: float, best_back_price: float, best_lay_price: float, last_publish_time) -> None:
        self.best_back_price = best_back_price
        self.best_lay_price = best_lay_price

        self.last_publish_time = last_publish_time

        self.ltp = current_price

        if self.last_publish_time is not None and self.enter_price is not None and self.order_placed_time is not None:
            if self.order_placed_time - last_publish_time > timedelta(seconds=1):
                return self.exit_position("timeout")

        if self.max_price is None and self.enter_price is not None:
            self.max_price = current_price

        if self.min_price is None and self.enter_price is not None:
            self.min_price = current_price

        if self.max_price is not None and current_price > self.max_price and self.enter_price is not None:
            self.max_price = current_price

        if self.min_price is not None and current_price < self.max_price and self.enter_price is not None:
            self.min_price = current_price

        if self.enter_price is None:
            return None

        if self.stop_loss_type == StopLossType.TRAILING:
            self._update_trailing_stop_loss(current_price)

        order = self._check_take_profit(current_price)
        if order is not None:
            return order

        return self._check_stop_loss(current_price)

    def _update_trailing_stop_loss(self, current_price: float) -> None:
        if self.trailing_stop_distance is None:
            raise Exception("Trailing stop distance must be non-null")

        if self.side == TradeSide.SHORT:  # Short position
            new_stop_loss = current_price + self.trailing_stop_distance
            if self.stop_loss_price is None or new_stop_loss > self.stop_loss_price:
                self.stop_loss_price = new_stop_loss
        else:  # Long position
            new_stop_loss = current_price - self.trailing_stop_distance
            if self.stop_loss_price is None or new_stop_loss < self.stop_loss_price:
                self.stop_loss_price = new_stop_loss

        logging.info(f"Stop loss price updated to: {self.stop_loss_price}")

    def _check_stop_loss(self, current_price: float) -> None:
        if self.stop_loss_price is None or self.enter_price is None:
            raise Exception(
                "Should not call stop loss if enter price is None")

        logging.info(
            f"Current price {current_price} enter price {self.enter_price} SL price {self.stop_loss_price} side {self.side}")

        if self.side == TradeSide.SHORT:  # Short position
            if current_price > self.stop_loss_price:
                return self.exit_position("Stop loss")
        else:  # Long position
            if current_price < self.stop_loss_price:
                return self.exit_position("Stop loss")

    def _check_take_profit(self, current_price: float) -> None:
        if self.take_profit_price is None or self.enter_price is None:
            raise Exception(
                "Should not call take profit if enter price is None")

        logging.info(
            f"Current price {current_price} enter price {self.enter_price} TP price {self.take_profit_price} side {self.side}")

        if self.side == TradeSide.SHORT:  # Short position
            if current_price <= self.take_profit_price:
                return self.exit_position("Take profit")  # Take profit
        else:  # Long position
            if current_price >= self.take_profit_price:
                return self.exit_position("Take profit")  # Take profit

    def total_pos_if_win_lose(self, orders):
        pos_if_win = 0
        pos_if_lose = 0

        for order in orders:
            pos_if_win += position_if_win(order)
            pos_if_lose += position_if_lose(order)

        return (pos_if_win, pos_if_lose)

    def calculate_cash_out(self, orders, back_odds, lay_odds) -> None:
        (pos_if_win, pos_if_lose) = self.total_pos_if_win_lose(orders)

        logging.info(f"Found pos if win / lose: {pos_if_win} {pos_if_lose}")

        take_odds = lay_odds if pos_if_win > pos_if_lose else back_odds

        stake = (pos_if_win - pos_if_lose) / (take_odds + 1)

        return (take_odds, stake if pos_if_win > pos_if_lose else -stake, "LAY" if pos_if_win > pos_if_lose else "BACK")

    def exit_position(self, reason) -> BetfairOrder:
        if self.open_order is not None:
            return None
        filled_orders = [
            order for order in self.orders if order.status == OrderStatus.EXECUTION_COMPLETE]

        (take_odds, stake, side) = self.calculate_cash_out(
            filled_orders, self.best_back_price, self.best_lay_price)

        logging.info(
            "Exiting position with size: %2f and side: %s and price: %2f enter price: %2f", stake, side, take_odds, self.enter_price)

        order = self.create_order(
            side=side,
            order_type=LimitOrder(price=take_odds, size=round(stake, 2)),
            notes=OrderedDict()
        )
        self.open_order = order.id
        order.notes['trigger'] = reason
        order.notes['side'] = self.side
        self.exit = True

        # print(f"Max price {self.max_price} min price: {self.min_price} exit price {take_odds} enter price {self.enter_price} TP {self.take_profit_price} side {self.side}")

        return order

    def enter_position(self, size: float) -> LimitOrder:
        # if we think the price will go up, we go long
        order_side = 'LAY' if self.side == TradeSide.LONG else 'BACK'
        order = self.create_order(
            side=order_side,
            order_type=LimitOrder(price=self.ltp, size=size),
            notes=OrderedDict()
        )
        self.enter_price = self.ltp

        self.take_profit_price = self.enter_price + \
            (self.enter_price * self.take_profit_percent) if self.side == TradeSide.LONG else self.enter_price - \
            (self.enter_price * self.take_profit_percent)

        self.open_order = order.id
        order.notes['trigger'] = "Enter position"
        order.notes['side'] = self.side

        logging.info("Entering into position with size: %2f and side: %s and price: %2f TP price: %2f",
                     size, self.side, self.ltp, self.take_profit_price)

        return order

    def update_orders(self, orders):
        for order in orders:
            if order.id == self.open_order and order.status == OrderStatus.EXECUTION_COMPLETE:
                self.open_order = None
                self.order_placed_time = self.last_publish_time

    def is_closed(self):
        return self.exit and self.open_order == None

    @property
    def info(self) -> dict:
        info = super().info
        info.update({
            'side': self.side.value,
            'stop_loss_price': self.stop_loss_price,
            'stop_loss_type': self.stop_loss_type.value if self.stop_loss_type else None,
            'trailing_stop_distance': self.trailing_stop_distance,
            'best_back_price': self.best_back_price,
            'best_lay_price': self.best_lay_price,
            'is_active': self.is_active,
        })
        return info
