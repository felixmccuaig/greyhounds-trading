def position_if_win(order):
    side = order.side
    price = order.order_type.price
    size = order.order_type.size

    pos = 0

    if side == 'BACK':
        pos += (price * size)
    elif side == 'LAY':
        pos -= (price * size)

    return pos


def position_if_lose(order):
    side = order.side
    size = order.order_type.size

    pos = 0

    if side == 'BACK':
        pos -= size
    elif side == 'LAY':
        pos += size

    return pos
