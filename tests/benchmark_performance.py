#!/usr/bin/env python3
"""
Performance benchmarks for loadshaper on resource-constrained VMs.

These benchmarks characterize the tool's behavior on target hardware
(1 vCPU/1GB RAM VMs) and determine optimal settings to maintain >20%
resource usage for Oracle reclamation prevention.
"""

import sys
import os
import time
import psutil
import subprocess
import json
import statistics
from typing import Dict, List, Tuple
import threading
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loadshaper


class LoadshaperBenchmark:
    """Benchmark suite for loadshaper performance characterization."""

    def __init__(self):
        self.results = {
            'system_info': self.get_system_info(),
            'baseline': {},
            'cpu_benchmarks': {},
            'memory_benchmarks': {},
            'network_benchmarks': {},
            'combined_benchmarks': {},
            'resource_efficiency': {},
            'recommendations': {}
        }

    def get_system_info(self) -> Dict:
        """Collect system information."""
        return {
            'cpu_count': psutil.cpu_count(),
            'cpu_freq': psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
            'memory_total_mb': psutil.virtual_memory().total // (1024 * 1024),
            'platform': sys.platform,
            'python_version': sys.version,
            'nice_support': os.nice(0) == 0  # Check if nice is supported
        }

    def measure_baseline(self, duration: int = 10) -> Dict:
        """Measure baseline system resource usage."""
        print(f"Measuring baseline for {duration}s...")
        samples = []

        for _ in range(duration):
            samples.append({
                'cpu_percent': psutil.cpu_percent(interval=1),
                'memory_percent': psutil.virtual_memory().percent,
                'network_bytes_sent': psutil.net_io_counters().bytes_sent,
                'network_bytes_recv': psutil.net_io_counters().bytes_recv,
                'load_average': os.getloadavg()[0]
            })

        return {
            'cpu_avg': statistics.mean(s['cpu_percent'] for s in samples),
            'cpu_p95': sorted(s['cpu_percent'] for s in samples)[int(len(samples) * 0.95)],
            'memory_avg': statistics.mean(s['memory_percent'] for s in samples),
            'load_avg': statistics.mean(s['load_average'] for s in samples)
        }

    def benchmark_cpu_workers(self) -> Dict:
        """Benchmark different numbers of CPU workers."""
        print("\nBenchmarking CPU workers...")
        results = {}

        for num_workers in [1, 2, 4, 8]:
            print(f"  Testing {num_workers} workers...")

            # Start loadshaper with specific worker count
            env = os.environ.copy()
            env['CPU_TARGET_PCT'] = '25'
            env['MEM_TARGET_PCT'] = '0'
            env['NET_TARGET_PCT'] = '0'
            env['NUM_WORKERS'] = str(num_workers)

            proc = subprocess.Popen(
                [sys.executable, 'loadshaper.py'],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            time.sleep(5)  # Let it stabilize

            # Measure performance
            samples = []
            for _ in range(10):
                samples.append({
                    'cpu': psutil.cpu_percent(interval=1),
                    'load': os.getloadavg()[0],
                    'latency': self.measure_response_latency()
                })

            proc.terminate()
            proc.wait(timeout=5)

            results[num_workers] = {
                'cpu_avg': statistics.mean(s['cpu'] for s in samples),
                'cpu_p95': sorted(s['cpu'] for s in samples)[int(len(samples) * 0.95)],
                'load_avg': statistics.mean(s['load'] for s in samples),
                'latency_ms': statistics.mean(s['latency'] for s in samples if s['latency'])
            }

        return results

    def benchmark_memory_occupation(self) -> Dict:
        """Benchmark memory occupation strategies."""
        print("\nBenchmarking memory occupation...")
        results = {}

        for target_pct in [20, 30, 40]:
            print(f"  Testing {target_pct}% memory target...")

            env = os.environ.copy()
            env['CPU_TARGET_PCT'] = '0'
            env['MEM_TARGET_PCT'] = str(target_pct)
            env['NET_TARGET_PCT'] = '0'

            proc = subprocess.Popen(
                [sys.executable, 'loadshaper.py'],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            time.sleep(10)  # Let memory stabilize

            # Measure memory usage
            samples = []
            for _ in range(5):
                mem = psutil.virtual_memory()
                samples.append({
                    'percent': mem.percent,
                    'available_mb': mem.available // (1024 * 1024),
                    'swap_percent': psutil.swap_memory().percent
                })
                time.sleep(1)

            proc.terminate()
            proc.wait(timeout=5)

            results[target_pct] = {
                'memory_avg': statistics.mean(s['percent'] for s in samples),
                'memory_stable': max(s['percent'] for s in samples) - min(s['percent'] for s in samples) < 5,
                'swap_used': any(s['swap_percent'] > 0 for s in samples)
            }

        return results

    def benchmark_network_generator(self) -> Dict:
        """Benchmark native network generator performance."""
        print("\nBenchmarking network generator...")
        results = {}

        # Test different packet sizes
        for packet_size in [64, 512, 1400]:
            print(f"  Testing {packet_size} byte packets...")

            gen = loadshaper.NetworkGenerator(
                target_addresses=['198.18.0.1'],  # RFC 2544 benchmark address
                port=12345,
                packet_size=packet_size,
                protocol='udp'
            )

            gen.start()

            # Measure actual throughput
            start_bytes = psutil.net_io_counters().bytes_sent
            time.sleep(5)
            end_bytes = psutil.net_io_counters().bytes_sent

            gen.stop()

            throughput_mbps = (end_bytes - start_bytes) * 8 / (5 * 1000000)

            results[f'packet_{packet_size}'] = {
                'throughput_mbps': throughput_mbps,
                'cpu_impact': psutil.cpu_percent(interval=0.1)
            }

        # Test TCP vs UDP
        for protocol in ['tcp', 'udp']:
            print(f"  Testing {protocol.upper()}...")

            gen = loadshaper.NetworkGenerator(
                target_addresses=['198.18.0.1'],
                port=12345,
                packet_size=1400,
                protocol=protocol
            )

            gen.start()
            time.sleep(5)

            cpu_samples = [psutil.cpu_percent(interval=0.5) for _ in range(4)]

            gen.stop()

            results[f'{protocol}_overhead'] = {
                'cpu_avg': statistics.mean(cpu_samples),
                'cpu_max': max(cpu_samples)
            }

        return results

    def benchmark_combined_load(self) -> Dict:
        """Benchmark combined CPU + Network load scenarios."""
        print("\nBenchmarking combined loads...")
        results = {}

        scenarios = [
            {'cpu': 25, 'mem': 0, 'net': 25, 'name': 'balanced'},
            {'cpu': 30, 'mem': 0, 'net': 15, 'name': 'cpu_heavy'},
            {'cpu': 15, 'mem': 0, 'net': 30, 'name': 'net_heavy'},
            {'cpu': 22, 'mem': 22, 'net': 22, 'name': 'triple_safe'}
        ]

        for scenario in scenarios:
            print(f"  Testing {scenario['name']}...")

            env = os.environ.copy()
            env['CPU_TARGET_PCT'] = str(scenario['cpu'])
            env['MEM_TARGET_PCT'] = str(scenario['mem'])
            env['NET_TARGET_PCT'] = str(scenario['net'])

            proc = subprocess.Popen(
                [sys.executable, 'loadshaper.py'],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            time.sleep(15)  # Let all components stabilize

            # Collect comprehensive metrics
            samples = []
            for _ in range(10):
                samples.append({
                    'cpu': psutil.cpu_percent(interval=1),
                    'memory': psutil.virtual_memory().percent,
                    'load': os.getloadavg()[0],
                    'net_bytes': psutil.net_io_counters().bytes_sent
                })

            proc.terminate()
            proc.wait(timeout=5)

            # Calculate network throughput
            net_throughput = (samples[-1]['net_bytes'] - samples[0]['net_bytes']) * 8 / (10 * 1000000)

            results[scenario['name']] = {
                'cpu_achieved': statistics.mean(s['cpu'] for s in samples),
                'memory_achieved': statistics.mean(s['memory'] for s in samples),
                'network_mbps': net_throughput,
                'load_avg': statistics.mean(s['load'] for s in samples),
                'targets_met': {
                    'cpu': statistics.mean(s['cpu'] for s in samples) >= scenario['cpu'] * 0.9,
                    'memory': scenario['mem'] == 0 or statistics.mean(s['memory'] for s in samples) >= scenario['mem'] * 0.9,
                    'network': net_throughput >= scenario['net'] * 0.9
                }
            }

        return results

    def measure_response_latency(self) -> float:
        """Measure system response latency during load."""
        try:
            start = time.perf_counter()
            subprocess.run(['echo', 'test'], capture_output=True, timeout=0.1)
            return (time.perf_counter() - start) * 1000  # Convert to ms
        except:
            return None

    def analyze_efficiency(self) -> Dict:
        """Analyze resource efficiency and overhead."""
        print("\nAnalyzing resource efficiency...")

        # Calculate efficiency metrics
        cpu_results = self.results.get('cpu_benchmarks', {})

        efficiency = {}

        if cpu_results:
            # Find optimal worker count (best CPU% per worker)
            worker_efficiency = {}
            for workers, metrics in cpu_results.items():
                if isinstance(workers, int) and metrics.get('cpu_avg'):
                    worker_efficiency[workers] = metrics['cpu_avg'] / workers

            if worker_efficiency:
                optimal_workers = max(worker_efficiency, key=worker_efficiency.get)
                efficiency['optimal_cpu_workers'] = optimal_workers
                efficiency['cpu_efficiency_per_worker'] = worker_efficiency[optimal_workers]

        # Analyze network efficiency
        net_results = self.results.get('network_benchmarks', {})
        if net_results:
            # Compare TCP vs UDP overhead
            tcp_overhead = net_results.get('tcp_overhead', {}).get('cpu_avg', 0)
            udp_overhead = net_results.get('udp_overhead', {}).get('cpu_avg', 0)

            if tcp_overhead and udp_overhead:
                efficiency['tcp_vs_udp_cpu_ratio'] = tcp_overhead / udp_overhead if udp_overhead else None
                efficiency['recommended_protocol'] = 'udp' if udp_overhead < tcp_overhead else 'tcp'

        return efficiency

    def generate_recommendations(self) -> Dict:
        """Generate configuration recommendations based on benchmarks."""
        print("\nGenerating recommendations...")

        recommendations = {
            'oracle_free_tier': {},
            'general_purpose': {},
            'warnings': []
        }

        # Oracle Free Tier specific recommendations
        sys_info = self.results['system_info']
        if sys_info['cpu_count'] <= 2 and sys_info['memory_total_mb'] <= 2048:
            # Likely a small VM
            recommendations['oracle_free_tier'] = {
                'cpu_target': 25,
                'mem_target': 0,  # E2 shapes don't need memory
                'net_target': 25,
                'num_workers': min(2, sys_info['cpu_count']),
                'rationale': 'Conservative settings for 1-2 vCPU VMs to maintain >20% usage without impacting system'
            }
        else:
            # Larger system
            recommendations['oracle_free_tier'] = {
                'cpu_target': 22,
                'mem_target': 0,
                'net_target': 22,
                'num_workers': min(4, sys_info['cpu_count']),
                'rationale': 'Settings for larger VMs with headroom for actual workloads'
            }

        # Check for potential issues
        baseline = self.results.get('baseline', {})
        if baseline.get('cpu_avg', 0) > 10:
            recommendations['warnings'].append(
                f"High baseline CPU usage ({baseline['cpu_avg']:.1f}%) detected. Consider lowering targets."
            )

        if baseline.get('load_avg', 0) > 0.5:
            recommendations['warnings'].append(
                f"System already under load (LA: {baseline['load_avg']:.2f}). Enable load monitoring."
            )

        # Efficiency-based recommendations
        efficiency = self.results.get('resource_efficiency', {})
        if efficiency.get('recommended_protocol'):
            recommendations['general_purpose']['network_protocol'] = efficiency['recommended_protocol']

        if efficiency.get('optimal_cpu_workers'):
            recommendations['general_purpose']['num_workers'] = efficiency['optimal_cpu_workers']

        return recommendations

    def save_results(self, filename: str = 'benchmark_results.json'):
        """Save benchmark results to JSON file."""
        with open(filename, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)
        print(f"\nResults saved to {filename}")

    def print_summary(self):
        """Print a summary of benchmark results."""
        print("\n" + "="*60)
        print("LOADSHAPER PERFORMANCE BENCHMARK SUMMARY")
        print("="*60)

        # System info
        sys_info = self.results['system_info']
        print(f"\nSystem: {sys_info['cpu_count']} CPUs, {sys_info['memory_total_mb']}MB RAM")

        # Baseline
        baseline = self.results.get('baseline', {})
        if baseline:
            print(f"\nBaseline Usage:")
            print(f"  CPU: {baseline.get('cpu_avg', 0):.1f}% avg, {baseline.get('cpu_p95', 0):.1f}% p95")
            print(f"  Memory: {baseline.get('memory_avg', 0):.1f}%")
            print(f"  Load Average: {baseline.get('load_avg', 0):.2f}")

        # CPU benchmarks
        cpu_bench = self.results.get('cpu_benchmarks', {})
        if cpu_bench:
            print(f"\nCPU Worker Performance:")
            for workers, metrics in sorted(cpu_bench.items()):
                if isinstance(workers, int):
                    print(f"  {workers} workers: {metrics.get('cpu_avg', 0):.1f}% CPU, "
                          f"{metrics.get('latency_ms', 0):.1f}ms latency")

        # Network benchmarks
        net_bench = self.results.get('network_benchmarks', {})
        if net_bench:
            print(f"\nNetwork Generator Performance:")
            for key, metrics in net_bench.items():
                if 'packet_' in key:
                    size = key.split('_')[1]
                    print(f"  {size}B packets: {metrics.get('throughput_mbps', 0):.1f} Mbps")

        # Combined scenarios
        combined = self.results.get('combined_benchmarks', {})
        if combined:
            print(f"\nCombined Load Scenarios:")
            for scenario, metrics in combined.items():
                targets_met = sum(metrics.get('targets_met', {}).values())
                total_targets = len(metrics.get('targets_met', {}))
                print(f"  {scenario}: {targets_met}/{total_targets} targets met, "
                      f"LA: {metrics.get('load_avg', 0):.2f}")

        # Recommendations
        recs = self.results.get('recommendations', {})
        if recs.get('oracle_free_tier'):
            print(f"\nRecommended Oracle Free Tier Settings:")
            oft = recs['oracle_free_tier']
            print(f"  CPU_TARGET_PCT={oft.get('cpu_target')}")
            print(f"  MEM_TARGET_PCT={oft.get('mem_target')}")
            print(f"  NET_TARGET_PCT={oft.get('net_target')}")
            print(f"  NUM_WORKERS={oft.get('num_workers')}")
            print(f"  Rationale: {oft.get('rationale')}")

        if recs.get('warnings'):
            print(f"\nWarnings:")
            for warning in recs['warnings']:
                print(f"  ⚠️  {warning}")

        print("\n" + "="*60)


def main():
    """Run the benchmark suite."""
    print("Starting Loadshaper Performance Benchmark Suite")
    print("This will take approximately 5-10 minutes...")

    benchmark = LoadshaperBenchmark()

    # Run benchmarks
    benchmark.results['baseline'] = benchmark.measure_baseline()
    benchmark.results['cpu_benchmarks'] = benchmark.benchmark_cpu_workers()
    benchmark.results['memory_benchmarks'] = benchmark.benchmark_memory_occupation()
    benchmark.results['network_benchmarks'] = benchmark.benchmark_network_generator()
    benchmark.results['combined_benchmarks'] = benchmark.benchmark_combined_load()
    benchmark.results['resource_efficiency'] = benchmark.analyze_efficiency()
    benchmark.results['recommendations'] = benchmark.generate_recommendations()

    # Save and display results
    benchmark.save_results()
    benchmark.print_summary()

    return 0


if __name__ == '__main__':
    sys.exit(main())