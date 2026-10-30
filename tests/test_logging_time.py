"""File and dashboard timestamps follow local timezone rules for each event."""
from datetime import datetime, timezone
import logging
import time

import pytest

from axispy import LocalTimeFormatter, local_timestamp
from axispy_web import LiveLogs, timestamp


@pytest.mark.parametrize(('month', 'offset'), [(1, '+11:00'), (7, '+10:00')])
def test_local_log_time_includes_daylight_saving(monkeypatch, month, offset):
    # POSIX timezone rules avoid relying on the test host's zoneinfo installation.
    with monkeypatch.context() as env:
        env.setenv('TZ', 'AEST-10AEDT,M10.1.0,M4.1.0/3')
        time.tzset()
        try:
            created = datetime(2026, month, 15, 0, 0, tzinfo=timezone.utc).timestamp()
            expected = f'2026-{month:02d}-15T{11 if month == 1 else 10}:00:00{offset}'
            record = logging.LogRecord('axispy', logging.INFO, '', 0, 'ready', (), None)
            record.created = created
            assert local_timestamp(created) == expected
            assert LocalTimeFormatter('%(asctime)s %(message)s').format(record) == expected + ' ready'
            logs = LiveLogs([])
            logs.emit(record)
            assert logs.since(0)['entries'][0]['time'] == expected
            assert datetime.fromisoformat(timestamp()).utcoffset() == datetime.now().astimezone().utcoffset()
        finally:
            # Restore the process timezone even when an assertion fails.
            env.undo()
            time.tzset()
