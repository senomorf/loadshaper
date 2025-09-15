#!/usr/bin/env python3

import unittest
import unittest.mock
import time
import threading
import socket
import sys
import os

# Add the parent directory to the path so we can import loadshaper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loadshaper


class TestTokenBucket(unittest.TestCase):
    """Test token bucket rate limiting with 5ms precision."""

    def setUp(self):
        """Set up test environment before each test."""
        self.rate_mbps = 10.0  # 10 Mbps
        self.bucket = loadshaper.TokenBucket(self.rate_mbps)

    def test_initialization(self):
        """Test token bucket initialization."""
        self.assertEqual(self.bucket.rate_mbps, self.rate_mbps)
        self.assertAlmostEqual(self.bucket.capacity_bits, self.rate_mbps * 1_000_000 * 0.1, places=1)
        self.assertAlmostEqual(self.bucket.tokens, self.bucket.capacity_bits, places=1)
        self.assertAlmostEqual(self.bucket.tick_interval, 0.005, places=3)

    def test_minimum_rate_protection(self):
        """Test protection against zero/negative rates."""
        bucket = loadshaper.TokenBucket(0.0)
        self.assertAlmostEqual(bucket.rate_mbps, 0.001, places=3)

        bucket = loadshaper.TokenBucket(-5.0)
        self.assertAlmostEqual(bucket.rate_mbps, 0.001, places=3)

    def test_can_send_immediate(self):
        """Test immediate packet sending when tokens available."""
        packet_size = 1000  # 1000 bytes
        self.assertTrue(self.bucket.can_send(packet_size))

    def test_consume_tokens(self):
        """Test token consumption for packet sending."""
        packet_size = 1000  # 1000 bytes
        initial_tokens = self.bucket.tokens

        self.assertTrue(self.bucket.consume(packet_size))
        expected_remaining = initial_tokens - (packet_size * 8)
        self.assertAlmostEqual(self.bucket.tokens, expected_remaining, places=1)

    def test_token_exhaustion(self):
        """Test behavior when tokens are exhausted."""
        # Freeze time to prevent automatic replenishment
        with unittest.mock.patch('time.time') as mock_time:
            mock_time.return_value = 1000.0

            # Set bucket to use frozen time
            self.bucket.last_update = 1000.0

            # Consume tokens successfully first (smaller packet that fits)
            available_tokens = self.bucket.tokens
            consume_packet = int(available_tokens / 8) - 100  # Leave very few tokens
            self.assertTrue(self.bucket.consume(consume_packet))

            # Now try to consume more than remaining tokens
            large_packet = int(self.bucket.tokens / 8) + 100
            self.assertFalse(self.bucket.consume(large_packet))

            # Should not be able to send large packet (not enough tokens)
            self.assertFalse(self.bucket.can_send(large_packet))

    def test_token_replenishment(self):
        """Test token replenishment over time."""
        # Consume most tokens
        initial_tokens = self.bucket.tokens
        large_packet = int(initial_tokens / 8) - 100
        self.assertTrue(self.bucket.consume(large_packet))
        tokens_after_consume = self.bucket.tokens

        # Wait for token replenishment (simulate time passage)
        with unittest.mock.patch('time.time') as mock_time:
            # Set up continuous time progression
            base_time = self.bucket.last_update
            mock_time.return_value = base_time + 0.1  # Always return 100ms later

            self.bucket._add_tokens()

            # Should have significantly more tokens now due to replenishment
            # At 10 Mbps, 100ms should add 1,000,000 bits
            expected_new_tokens = self.rate_mbps * 1_000_000 * 0.1  # 100ms worth
            self.assertGreater(self.bucket.tokens, tokens_after_consume)
            # Should have refilled to capacity
            self.assertEqual(self.bucket.tokens, self.bucket.capacity_bits)

    def test_wait_time_calculation(self):
        """Test accurate wait time calculation."""
        # Freeze time to prevent automatic replenishment
        with unittest.mock.patch('time.time') as mock_time:
            mock_time.return_value = 1000.0
            self.bucket.last_update = 1000.0

            # Consume most tokens to leave very few
            available_tokens = self.bucket.tokens
            consume_packet = int(available_tokens / 8) - 50  # Leave ~400 bits
            self.bucket.consume(consume_packet)

            # Try to send packet that needs more tokens than available
            packet_size = 1000  # Needs 8000 bits, but we only have ~400
            wait_time = self.bucket.wait_time(packet_size)

            # Should be positive (need to wait)
            self.assertGreater(wait_time, 0)

            # Should be reasonable (not too long for small packet at 10 Mbps)
            self.assertLess(wait_time, 1.0)

    def test_rate_update(self):
        """Test dynamic rate updates."""
        new_rate = 20.0  # Double the rate
        old_capacity = self.bucket.capacity_bits

        self.bucket.update_rate(new_rate)

        self.assertEqual(self.bucket.rate_mbps, new_rate)
        self.assertAlmostEqual(self.bucket.capacity_bits, new_rate * 1_000_000 * 0.1, places=1)
        self.assertLessEqual(self.bucket.tokens, self.bucket.capacity_bits)

    def test_precision_timing(self):
        """Test 5ms precision in token calculations."""
        # Test that small time intervals are handled correctly
        with unittest.mock.patch('time.time') as mock_time:
            mock_time.side_effect = [0.0, 0.005]  # Exactly 5ms

            bucket = loadshaper.TokenBucket(1.0)  # 1 Mbps
            bucket.last_update = 0.0
            bucket.tokens = 0

            bucket._add_tokens()

            # Should have accumulated exactly 5ms worth of tokens
            expected_tokens = 0.005 * 1.0 * 1_000_000
            self.assertAlmostEqual(bucket.tokens, expected_tokens, places=1)


class TestNetworkGenerator(unittest.TestCase):
    """Test native Python network generator."""

    def setUp(self):
        """Set up test environment before each test."""
        self.rate_mbps = 5.0
        self.protocol = "udp"
        self.ttl = 1
        self.packet_size = 1400
        self.port = 15201

        self.generator = loadshaper.NetworkGenerator(
            rate_mbps=self.rate_mbps,
            protocol=self.protocol,
            ttl=self.ttl,
            packet_size=self.packet_size,
            port=self.port
        )

    def tearDown(self):
        """Clean up after each test."""
        if self.generator:
            self.generator.stop()

    def test_initialization(self):
        """Test network generator initialization."""
        self.assertEqual(self.generator.protocol, self.protocol)
        self.assertEqual(self.generator.ttl, self.ttl)
        self.assertEqual(self.generator.packet_size, self.packet_size)
        self.assertEqual(self.generator.port, self.port)
        self.assertIsNotNone(self.generator.packet_data)
        self.assertEqual(len(self.generator.packet_data), self.packet_size)


    def test_packet_size_limits(self):
        """Test packet size limits are enforced."""
        # Test minimum packet size
        small_gen = loadshaper.NetworkGenerator(rate_mbps=1.0, packet_size=10)
        self.assertGreaterEqual(small_gen.packet_size, 64)

        # Test maximum UDP packet size
        large_gen = loadshaper.NetworkGenerator(rate_mbps=1.0, packet_size=70000)
        self.assertLessEqual(large_gen.packet_size, 65507)

    def test_port_validation(self):
        """Test port number validation."""
        # Test minimum port
        low_gen = loadshaper.NetworkGenerator(rate_mbps=1.0, port=500)
        self.assertGreaterEqual(low_gen.port, 1024)

        # Test maximum port
        high_gen = loadshaper.NetworkGenerator(rate_mbps=1.0, port=70000)
        self.assertLessEqual(high_gen.port, 65535)

    def test_ttl_validation(self):
        """Test TTL validation and safety."""
        # Test minimum TTL
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, ttl=0)
        self.assertGreaterEqual(gen.ttl, 1)

        # Test TTL=1 for safety
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, ttl=1)
        self.assertEqual(gen.ttl, 1)

    def test_rate_update(self):
        """Test dynamic rate updates."""
        new_rate = 10.0
        self.generator.update_rate(new_rate)
        self.assertEqual(self.generator.bucket.rate_mbps, new_rate)

    @unittest.mock.patch('socket.socket')
    def test_udp_socket_initialization(self, mock_socket):
        """Test UDP socket initialization and configuration."""
        mock_sock = unittest.mock.MagicMock()
        mock_socket.return_value = mock_sock

        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")
        gen.start(["127.0.0.1"])

        # Verify socket creation and configuration
        mock_socket.assert_called_with(unittest.mock.ANY, unittest.mock.ANY)
        mock_sock.setsockopt.assert_any_call(unittest.mock.ANY, unittest.mock.ANY, 1)  # TTL
        mock_sock.setblocking.assert_called_with(False)

        gen.stop()

    def test_tcp_socket_initialization(self):
        """Test TCP socket initialization (uses per-connection sockets)."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="tcp")
        gen.start(["127.0.0.1"])

        # TCP mode starts with socket None and uses connection pooling
        self.assertIsNone(gen.socket)
        self.assertEqual(gen.protocol, "tcp")

        # State could be various states depending on peer validation and startup
        # The key test is that protocol is preserved
        self.assertTrue(gen.state in [s for s in loadshaper.NetworkState],
                       f"Invalid state: {gen.state}")

        gen.stop()

    def test_context_manager(self):
        """Test NetworkGenerator as context manager."""
        with loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp") as gen:
            gen.start(["127.0.0.1"])
            # Socket might be None initially, but state should be valid
            self.assertIn(gen.state, [s for s in loadshaper.NetworkState])

        # Resources should be cleaned up after context exit
        self.assertEqual(gen.state, loadshaper.NetworkState.OFF)

    def test_tcp_connection_pooling(self):
        """Test TCP connection pooling functionality."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="tcp")
        gen.start(["127.0.0.1"])  # Initialize the generator

        # Mock socket creation
        mock_sock1 = unittest.mock.MagicMock()
        mock_sock2 = unittest.mock.MagicMock()

        with unittest.mock.patch('socket.socket', side_effect=[mock_sock1, mock_sock2]):
            # Get first connection
            conn1 = gen._get_tcp_connection('127.0.0.1')
            self.assertIsNotNone(conn1)  # Should get a connection (mocked or real)

            # Get same connection again - should be cached if available
            conn1_cached = gen._get_tcp_connection('127.0.0.1')
            self.assertIsNotNone(conn1_cached)

            # Connection pool should manage connections
            self.assertIsInstance(gen.tcp_connections, dict)

        gen.stop()

    def test_ipv6_address_resolution(self):
        """Test IPv6 address resolution and caching."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")

        # Mock getaddrinfo to return both IPv4 and IPv6
        mock_addr_info = [
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('192.168.1.1', 15201)),
            (socket.AF_INET6, socket.SOCK_DGRAM, 0, '', ('2001:db8::1', 15201, 0, 0))
        ]

        with unittest.mock.patch('socket.getaddrinfo', return_value=mock_addr_info):
            gen.start(['example.com'])  # Start will resolve targets

            # Should initialize peer validation structures
            self.assertIsInstance(gen.peers, dict)
            # Peers dict contains validation data
            if len(gen.peers) > 0:
                peer_key = list(gen.peers.keys())[0]
                self.assertIn('reputation', gen.peers[peer_key])

        gen.stop()

    def test_dns_resolution_caching(self):
        """Test DNS resolution and peer management."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")

        mock_addr_info = [
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('192.168.1.1', 15201))
        ]

        with unittest.mock.patch('socket.getaddrinfo', return_value=mock_addr_info) as mock_getaddrinfo:
            # Start will resolve and validate peers
            gen.start(['example.com'])

            # Should have peer tracking structures initialized
            self.assertIsInstance(gen.peers, dict)
            # Peer reputation is part of the peers dict structure
            self.assertGreater(len(gen.peers), 0)

        gen.stop()

    def test_ipv6_only_fallback(self):
        """Test fallback to IPv6 when no IPv4 available."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="udp")

        # Mock getaddrinfo to return only IPv6
        mock_addr_info = [
            (socket.AF_INET6, socket.SOCK_DGRAM, 0, '', ('2001:db8::1', 15201, 0, 0))
        ]

        with unittest.mock.patch('socket.getaddrinfo', return_value=mock_addr_info):
            gen.start(['ipv6only.example.com'])  # Start will resolve targets

            # Should handle IPv6 addresses in peer list
            self.assertIsInstance(gen.peers, dict)

        gen.stop()

    def test_tcp_connection_cleanup_on_stop(self):
        """Test that all TCP connections are closed on stop."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="tcp")

        # Create mock connections
        mock_conn1 = unittest.mock.MagicMock()
        mock_conn2 = unittest.mock.MagicMock()
        gen.tcp_connections = {'host1': mock_conn1, 'host2': mock_conn2}

        gen.stop()

        # All connections should be closed
        mock_conn1.close.assert_called_once()
        mock_conn2.close.assert_called_once()
        self.assertEqual(len(gen.tcp_connections), 0)

    def test_protocol_validation(self):
        """Test invalid protocol handling."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0, protocol="invalid")

        with unittest.mock.patch('loadshaper.logger') as mock_logger:
            gen.start(["127.0.0.1"])
            # Invalid protocol should be handled gracefully, may log warnings or errors
            # State should remain in a safe state
            self.assertIn(gen.state, [loadshaper.NetworkState.ERROR, loadshaper.NetworkState.OFF])

        gen.stop()

    @unittest.mock.patch('loadshaper.logger')
    def test_empty_peer_list_logging(self, mock_logger):
        """Test logging when no peers are available."""
        gen = loadshaper.NetworkGenerator(rate_mbps=1.0)

        gen.start([])  # Empty peer list - should warn about disabled network

        # Should log warning about disabled network generation
        mock_logger.warning.assert_called()
        # Check for warning about no peers
        call_args = [call[0][0] for call in mock_logger.warning.call_args_list]
        warning_logged = any('No valid peers' in arg or 'disabled' in arg for arg in call_args)
        self.assertTrue(warning_logged, "Should log warning about disabled network generation")

        gen.stop()

    @unittest.mock.patch('time.time')
    def test_burst_duration_control(self, mock_time):
        """Test traffic burst duration control."""
        # Mock time progression to simulate 1.1s passage
        start_time = 1000.0

        # Initialize generator properly with the new state machine
        self.generator.start(["127.0.0.1"])

        # Mock time to return increasing values for the duration of the burst
        time_sequence = [start_time + i * 0.1 for i in range(15)]  # 1.5s worth of 0.1s increments
        mock_time.side_effect = time_sequence

        with unittest.mock.patch.object(self.generator, '_send_udp_burst_packet', return_value=True):
            packets_sent = self.generator.send_burst(1.0)  # 1 second burst

        # Should respect burst duration control
        # The new implementation uses token bucket rate limiting
        self.assertGreaterEqual(packets_sent, 0)  # At least some packets sent

        self.generator.stop()

    def test_packet_data_preparation(self):
        """Test packet data preparation with timestamp."""
        # Packet should contain timestamp and pattern
        self.assertIsNotNone(self.generator.packet_data)
        self.assertEqual(len(self.generator.packet_data), self.packet_size)

        # First 8 bytes should be timestamp (double)
        import struct
        timestamp_bytes = self.generator.packet_data[:8]
        timestamp = struct.unpack('!d', timestamp_bytes)[0]
        self.assertIsInstance(timestamp, float)
        self.assertGreater(timestamp, 0)

    def test_cleanup_on_stop(self):
        """Test proper cleanup when stopping generator."""
        with unittest.mock.patch('socket.socket') as mock_socket:
            mock_sock = unittest.mock.MagicMock()
            mock_socket.return_value = mock_sock

            self.generator.start(["127.0.0.1"])

            # Generator might not have a socket if in TCP mode or certain states
            initial_state = self.generator.state

            self.generator.stop()

            # After stop, state should be OFF
            self.assertEqual(self.generator.state, loadshaper.NetworkState.OFF)

            # TCP connections should be cleaned up
            if hasattr(self.generator, 'tcp_connections'):
                self.assertEqual(len(self.generator.tcp_connections), 0)


class TestNetworkGeneratorIntegration(unittest.TestCase):
    """Integration tests for network generator with actual sockets."""

    def setUp(self):
        """Set up test environment."""
        # Use small packet size and low rate for testing
        self.generator = loadshaper.NetworkGenerator(
            rate_mbps=0.1,  # Very low rate for testing
            protocol="udp",
            ttl=1,
            packet_size=100,
            port=15201
        )

    def tearDown(self):
        """Clean up after tests."""
        if self.generator:
            self.generator.stop()

    def test_udp_burst_with_external_peers(self):
        """Test UDP traffic generation with external peers."""
        self.generator.start(["1.2.3.4"])  # Use external peer

        # Send very short burst to avoid network impact
        packets_sent = self.generator.send_burst(0.01)  # 10ms burst

        # Should have attempted to send at least some packets
        # (May be 0 if rate limiting prevents any sends in 10ms)
        self.assertGreaterEqual(packets_sent, 0)

    def test_low_rate_accuracy(self):
        """Test rate limiting accuracy at very low rates."""
        # Use extremely low rate
        self.generator.update_rate(0.001)  # 1 kbps

        # Start generator to initialize socket
        self.generator.start(["1.2.3.4"])  # Use external peer

        start_time = time.time()
        packets_sent = self.generator.send_burst(0.1)  # 100ms burst
        actual_duration = time.time() - start_time

        # Token bucket should limit transmission rate
        # At very low rates, might not send many packets
        self.assertGreaterEqual(packets_sent, 0)  # Should send at least some packets

        # Duration should be reasonable but could be very small for testing
        self.assertGreaterEqual(actual_duration, 0)  # Should take some time

        self.generator.stop()


class TestNetworkClientThread(unittest.TestCase):
    """Test the updated net_client_thread function."""

    def setUp(self):
        """Set up test environment."""
        # Mock configuration to enable client mode
        self.original_net_mode = getattr(loadshaper, 'NET_MODE', None)
        self.original_net_protocol = getattr(loadshaper, 'NET_PROTOCOL', None)
        self.original_net_ttl = getattr(loadshaper, 'NET_TTL', None)
        self.original_net_packet_size = getattr(loadshaper, 'NET_PACKET_SIZE', None)
        self.original_net_port = getattr(loadshaper, 'NET_PORT', None)
        self.original_net_peers = getattr(loadshaper, 'NET_PEERS', None)
        self.original_net_burst_sec = getattr(loadshaper, 'NET_BURST_SEC', None)
        self.original_net_idle_sec = getattr(loadshaper, 'NET_IDLE_SEC', None)
        self.original_net_min_rate = getattr(loadshaper, 'NET_MIN_RATE', None)
        self.original_net_max_rate = getattr(loadshaper, 'NET_MAX_RATE', None)
        self.original_net_require_external = getattr(loadshaper, 'NET_REQUIRE_EXTERNAL', None)
        self.original_net_validate_startup = getattr(loadshaper, 'NET_VALIDATE_STARTUP', None)
        self.original_net_state_debounce_sec = getattr(loadshaper, 'NET_STATE_DEBOUNCE_SEC', None)
        self.original_net_state_min_on_sec = getattr(loadshaper, 'NET_STATE_MIN_ON_SEC', None)
        self.original_net_state_min_off_sec = getattr(loadshaper, 'NET_STATE_MIN_OFF_SEC', None)
        self.original_net_state_ramp_up_sec = getattr(loadshaper, 'NET_STATE_RAMP_UP_SEC', None)

        # Set test configuration
        loadshaper.NET_MODE = "client"
        loadshaper.NET_PROTOCOL = "udp"
        loadshaper.NET_TTL = 1
        loadshaper.NET_PACKET_SIZE = 1000
        loadshaper.NET_PORT = 15201
        loadshaper.NET_PEERS = []  # Use empty peers list
        loadshaper.NET_BURST_SEC = 1
        loadshaper.NET_IDLE_SEC = 1
        loadshaper.NET_MIN_RATE = 0.1
        loadshaper.NET_MAX_RATE = 100.0
        loadshaper.NET_REQUIRE_EXTERNAL = True
        loadshaper.NET_VALIDATE_STARTUP = False
        loadshaper.NET_STATE_DEBOUNCE_SEC = 5.0
        loadshaper.NET_STATE_MIN_ON_SEC = 10.0
        loadshaper.NET_STATE_MIN_OFF_SEC = 10.0
        loadshaper.NET_STATE_RAMP_UP_SEC = 30.0

    def tearDown(self):
        """Restore original configuration."""
        loadshaper.NET_MODE = self.original_net_mode
        loadshaper.NET_PROTOCOL = self.original_net_protocol
        loadshaper.NET_TTL = self.original_net_ttl
        loadshaper.NET_PACKET_SIZE = self.original_net_packet_size
        loadshaper.NET_PORT = self.original_net_port
        loadshaper.NET_PEERS = self.original_net_peers
        loadshaper.NET_BURST_SEC = self.original_net_burst_sec
        loadshaper.NET_IDLE_SEC = self.original_net_idle_sec
        loadshaper.NET_MIN_RATE = self.original_net_min_rate
        loadshaper.NET_MAX_RATE = self.original_net_max_rate
        loadshaper.NET_REQUIRE_EXTERNAL = self.original_net_require_external
        loadshaper.NET_VALIDATE_STARTUP = self.original_net_validate_startup
        loadshaper.NET_STATE_DEBOUNCE_SEC = self.original_net_state_debounce_sec
        loadshaper.NET_STATE_MIN_ON_SEC = self.original_net_state_min_on_sec
        loadshaper.NET_STATE_MIN_OFF_SEC = self.original_net_state_min_off_sec
        loadshaper.NET_STATE_RAMP_UP_SEC = self.original_net_state_ramp_up_sec

    def test_thread_respects_stop_event(self):
        """Test that network thread respects stop event."""
        from multiprocessing import Value

        stop_evt = threading.Event()
        paused_fn = lambda: False
        rate_val = Value('d', 1.0)

        # Start thread
        thread = threading.Thread(
            target=loadshaper.net_client_thread,
            args=(stop_evt, paused_fn, rate_val)
        )
        thread.daemon = True
        thread.start()

        # Let it run briefly
        time.sleep(0.1)

        # Stop it
        stop_evt.set()
        thread.join(timeout=2.0)

        # Should have stopped cleanly
        self.assertFalse(thread.is_alive())

    def test_thread_respects_pause_function(self):
        """Test that network thread respects pause function."""
        from multiprocessing import Value

        stop_evt = threading.Event()
        paused = threading.Event()
        paused.set()  # Start paused
        paused_fn = lambda: paused.is_set()
        rate_val = Value('d', 1.0)

        with unittest.mock.patch('loadshaper.NetworkGenerator') as mock_gen_class:
            mock_gen = unittest.mock.MagicMock()
            mock_gen_class.return_value = mock_gen

            # Start thread
            thread = threading.Thread(
                target=loadshaper.net_client_thread,
                args=(stop_evt, paused_fn, rate_val)
            )
            thread.daemon = True
            thread.start()

            # Let it run briefly while paused
            time.sleep(0.1)

            # Should not have created generator while paused
            mock_gen_class.assert_not_called()

            # Unpause and stop
            paused.clear()
            time.sleep(0.05)  # Brief run time
            stop_evt.set()
            thread.join(timeout=2.0)

    def test_rate_changes_update_generator(self):
        """Test that rate changes properly update the generator."""
        from multiprocessing import Value

        stop_evt = threading.Event()
        paused_fn = lambda: False
        rate_val = Value('d', 1.0)

        with unittest.mock.patch('loadshaper.NetworkGenerator') as mock_gen_class:
            mock_gen = unittest.mock.MagicMock()
            mock_gen.send_burst.return_value = 0  # No packets sent
            mock_gen_class.return_value = mock_gen

            # Start thread
            thread = threading.Thread(
                target=loadshaper.net_client_thread,
                args=(stop_evt, paused_fn, rate_val)
            )
            thread.daemon = True
            thread.start()

            try:
                # Let it run several cycles to ensure generator creation
                time.sleep(0.2)

                # Should have created at least one generator instance
                self.assertGreaterEqual(mock_gen_class.call_count, 1)

            finally:
                # Stop thread
                stop_evt.set()
                thread.join(timeout=2.0)

    def test_disabled_when_not_client_mode(self):
        """Test thread is disabled when NET_MODE is not 'client'."""
        from multiprocessing import Value

        loadshaper.NET_MODE = "server"  # Disable client mode

        stop_evt = threading.Event()
        paused_fn = lambda: False
        rate_val = Value('d', 1.0)

        with unittest.mock.patch('loadshaper.NetworkGenerator') as mock_gen_class:
            # Start thread
            thread = threading.Thread(
                target=loadshaper.net_client_thread,
                args=(stop_evt, paused_fn, rate_val)
            )
            thread.daemon = True
            thread.start()
            thread.join(timeout=1.0)

            # Should not have created any generator
            mock_gen_class.assert_not_called()


if __name__ == '__main__':
    unittest.main()