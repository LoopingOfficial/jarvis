import unittest
from logic import ping_response

class PingTests(unittest.TestCase):
    def test_round_trip_latency(self):
        self.assertEqual(ping_response(.042), "Pong ! Latence : 42 ms.")
    def test_negative_latency_is_clamped(self):
        self.assertEqual(ping_response(-1), "Pong ! Latence : 0 ms.")

if __name__ == "__main__":
    unittest.main()
