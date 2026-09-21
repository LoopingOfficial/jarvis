def ping_response(latency):
    return f"Pong ! Latence : {max(0, round(latency * 1000))} ms."
