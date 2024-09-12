from collections import defaultdict
import os
import betfairlightweight
from flumine import FlumineSimulation, clients
import logging
from betfairlightweight.filters import streaming_market_data_filter

from src.strategy.strategy import MovingAverageStrategy
from src.strategy.market_making import MarketMakingStrategy

# Configure logging
logging.basicConfig(
    filename="historical_momentum_trader.log",
    level=logging.ERROR,
    format="%(asctime)s - %(message)s",
)

# Set up the Betfair client
trading = betfairlightweight.APIClient("", "", "")
client = clients.SimulatedClient(min_bet_validation=False)
framework = FlumineSimulation(client=client)

markets_folder = "markets"
market_files = os.listdir(markets_folder)
market_ids = ["markets/" + file for file in market_files]

print(f"Processing: {len(market_ids)} markets")

strategy = MovingAverageStrategy(
    market_filter={"markets": market_ids},
    market_data_filter=streaming_market_data_filter(
        fields=["EX_BEST_OFFERS", "EX_LTP", "EX_MARKET_DEF"]
    ),
    long_window=100,
    short_window=35,
    max_live_trade_count=100000,
    max_selection_exposure=10000000,
    # max_liability=1000000,
    max_order_exposure=10000,
    price_threshold=0.01
)

strategy = MarketMakingStrategy(
    max_live_trade_count=100000,
    max_selection_exposure=10000000,
    max_order_exposure=10000,
    market_filter={"markets": market_ids[0:3]},
    market_data_filter=streaming_market_data_filter(
        fields=["EX_BEST_OFFERS", "EX_LTP", "EX_MARKET_DEF"]
    ),
)

framework.add_strategy(strategy)
framework.run()

total_pnl = 0

for market in framework.markets:
    pnl = sum([o.profit for o in market.blotter])
    total_pnl += pnl
    print(
        f"Profit: {pnl:.2f} {market.market_id} {market.market_book.market_definition.market_type}")

    # Create a dictionary to group orders by selection_id
    orders_by_selection_id = defaultdict(list)

    # Group orders by selection_id
    for order in market.blotter:
        if order.size_matched == 0:
            continue
        orders_by_selection_id[order.selection_id].append(order)

    # Print the grouped orders
    for selection_id, orders in orders_by_selection_id.items():
        for order in orders:
            print(
                order.selection_id,
                order.side,
                order.responses.date_time_placed,
                order.date_time_execution_complete,
                order.status,
                order.order_type.price,
                order.average_price_matched,
                order.size_matched,
                order.profit,
                # order.notes['trigger'],
                # order.notes['side']
            )
        print("-" * 40)  # Separator between different selection IDs

print("Total PNL: {0:.2f}".format(total_pnl))
