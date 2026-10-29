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
    assert usage.snapshot() == dict(cpu_percent=None, cpu_status='unavailable', memory=None)
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
