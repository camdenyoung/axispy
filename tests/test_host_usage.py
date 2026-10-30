from axispy_web import HostUsage
import pytest


def write_proc(root, cpu='cpu 100 0 50 850 0 0 0 0 10 0\n',
               memory='MemTotal: 8192 kB\nMemAvailable: 2048 kB\n'):
    (root / 'stat').write_text(cpu)
    (root / 'meminfo').write_text(memory)


def test_host_cpu_deltas_cache_and_available_memory(tmp_path):
    write_proc(tmp_path)
    clock = [0.]
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    first = usage.snapshot()
    assert first['cpu_percent'] is None
    assert first['cpu_status'] == 'measuring'
    assert first['memory'] == {'used_bytes': 6144 * 1024, 'total_bytes': 8192 * 1024, 'percent': 75.0}
    # Guest ticks are already included in user ticks; iowait counts as idle.
    write_proc(tmp_path, cpu='cpu 130 0 70 890 10 0 0 0 40 0\n')
    clock[0] = .5
    assert usage.snapshot() == first
    clock[0] = 1.
    second = usage.snapshot()
    assert second['cpu_percent'] == 50.0
    assert second['cpu_status'] == 'ok'
    # Callers cannot mutate the cached memory reading.
    second['memory']['percent'] = 0
    assert usage.snapshot()['memory']['percent'] == 75.0


@pytest.mark.parametrize('cpu', ['', 'cpu0 1 2 3 4\n', 'cpu 1 2\n', 'cpu a 0 0 1\n', 'cpu -1 0 0 1\n'])
def test_bad_cpu_statistics_do_not_break_memory(tmp_path, cpu):
    write_proc(tmp_path, cpu=cpu)
    result = HostUsage(tmp_path).snapshot()
    assert result['cpu_percent'] is None
    assert result['cpu_status'] == 'unavailable'
    assert result['memory']['percent'] == 75.0


@pytest.mark.parametrize('memory', ['', 'MemTotal: 0 kB\nMemAvailable: 0 kB\n',
                                    'MemTotal: 10 kB\nMemAvailable: 11 kB\n',
                                    'MemTotal: 10 kB\nMemAvailable: -1 kB\n',
                                    'MemTotal: 10 bytes\nMemAvailable: 5 kB\n',
                                    'MemTotal: invalid kB\nMemAvailable: 5 kB\n'])
def test_bad_memory_statistics_are_unavailable(tmp_path, memory):
    write_proc(tmp_path, memory=memory)
    assert HostUsage(tmp_path).snapshot()['memory'] is None


def test_missing_proc_statistics_recover(tmp_path):
    clock = [0.]
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    assert usage.snapshot() == dict(cpu_percent=None, cpu_status='unavailable', memory=None, network=None, network_status='unavailable')
    write_proc(tmp_path)
    clock[0] += 1
    assert usage.snapshot()['cpu_status'] == 'measuring'
    write_proc(tmp_path, cpu='cpu 110 0 50 940 0 0 0 0\n')
    clock[0] += 1
    assert usage.snapshot()['cpu_percent'] == 10.0


def test_cpu_counter_reset_restarts_measurement(tmp_path):
    write_proc(tmp_path)
    clock = [0.]
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    usage.snapshot()
    write_proc(tmp_path, cpu='cpu 0 0 0 10 0 0 0 0\n')
    clock[0] += 1
    assert usage.snapshot()['cpu_status'] == 'measuring'
    write_proc(tmp_path, cpu='cpu 10 0 0 20 0 0 0 0\n')
    clock[0] += 1
    assert usage.snapshot()['cpu_percent'] == 50.0


def write_network(root, interfaces):
    (root / 'net').mkdir(exist_ok=True)
    lines = ['Inter-| Receive | Transmit', ' face | bytes packets errs drop fifo frame compressed multicast | bytes packets errs drop fifo colls carrier compressed']
    for name, (received, sent) in interfaces.items():
        fields = [0] * 16
        fields[0], fields[8] = received, sent
        lines.append(f'{name}: ' + ' '.join(str(value) for value in fields))
    (root / 'net' / 'dev').write_text('\n'.join(lines) + '\n')


def test_network_rates_use_elapsed_time_decimal_megabits_and_exclude_loopback(tmp_path):
    clock = [0.]
    write_network(tmp_path, {'eth0': (100, 200), 'eth1': (300, 400), 'lo': (10, 20)})
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    first = usage.snapshot()
    assert first['network'] is None
    assert first['network_status'] == 'measuring'
    write_network(tmp_path, {'eth0': (500100, 200), 'eth1': (750300, 500400), 'lo': (10**12, 10**12)})
    clock[0] = .5
    assert usage.snapshot() == first  # Shared cache does not resample too early.
    clock[0] = 2
    result = usage.snapshot()
    assert result['network'] == {'rx_mbps': 5.0, 'tx_mbps': 2.0}
    assert result['network_status'] == 'ok'
    result['network']['rx_mbps'] = 999
    assert usage.snapshot()['network']['rx_mbps'] == 5.0
    clock[0] = 3
    assert usage.snapshot()['network'] == {'rx_mbps': 0.0, 'tx_mbps': 0.0}


def test_network_counter_reset_rebaselines_without_spikes(tmp_path):
    clock = [0.]
    write_network(tmp_path, {'eth0': (1000000, 2000000)})
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    usage.snapshot()
    write_network(tmp_path, {'eth0': (10, 20)})
    clock[0] = 1
    assert usage.snapshot()['network_status'] == 'measuring'
    write_network(tmp_path, {'eth0': (125010, 250020)})
    clock[0] = 2
    assert usage.snapshot()['network'] == {'rx_mbps': 1.0, 'tx_mbps': 2.0}


def test_network_new_and_removed_interfaces_do_not_inflate_rates(tmp_path):
    clock = [0.]
    write_network(tmp_path, {'eth0': (0, 0), 'old': (10**9, 10**9)})
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    usage.snapshot()
    write_network(tmp_path, {'eth0': (125000, 250000), 'new': (10**12, 10**12)})
    clock[0] = 1
    assert usage.snapshot()['network'] == {'rx_mbps': 1.0, 'tx_mbps': 2.0}
    write_network(tmp_path, {'lo': (10**15, 10**15)})
    clock[0] = 2
    assert usage.snapshot()['network'] == {'rx_mbps': 0.0, 'tx_mbps': 0.0}


def test_network_read_failure_and_recovery_do_not_break_host_metrics(tmp_path):
    clock = [0.]
    write_proc(tmp_path)
    usage = HostUsage(tmp_path, clock=lambda: clock[0])
    assert usage.snapshot()['network_status'] == 'unavailable'
    write_network(tmp_path, {'eth0': (0, 0)})
    clock[0] = 1
    assert usage.snapshot()['network_status'] == 'measuring'
    write_network(tmp_path, {'eth0': (125000, 250000)})
    clock[0] = 2
    assert usage.snapshot()['network']['rx_mbps'] == 1.0
    (tmp_path / 'net' / 'dev').unlink()
    clock[0] = 3
    result = usage.snapshot()
    assert result['network_status'] == 'unavailable'
    assert result['memory']['percent'] == 75.0
    write_network(tmp_path, {'eth0': (10**12, 10**12)})
    clock[0] = 4
    assert usage.snapshot()['network_status'] == 'measuring'


@pytest.mark.parametrize('content', ['', 'invalid\nheader\n',
    'Inter-| Receive | Transmit\nheader\neth0: 1 2 3\n',
    'Inter-| Receive | Transmit\nheader\neth0: -1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n'])
def test_bad_network_counters_are_unavailable(tmp_path, content):
    write_network(tmp_path, {})
    (tmp_path / 'net' / 'dev').write_text(content)
    assert HostUsage(tmp_path).snapshot()['network_status'] == 'unavailable'
