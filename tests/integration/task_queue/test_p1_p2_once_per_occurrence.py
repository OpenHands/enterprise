"""P1 and P2: every occurrence runs exactly once across two replicas."""

import pytest

from conftest import FAKETIME, MANIFEST

OCCURRENCES = 12
INTERVAL = 10


def occurrence_report(stack) -> dict:
    rows = stack.sql(
        "SELECT extract(epoch FROM slot)::bigint, count(*), string_agg(replica, ',') "
        "FROM poc_job_runs WHERE job = 'tick' GROUP BY slot ORDER BY slot"
    )
    slots = [int(r[0]) for r in rows]
    # Ignore the first and last occurrence: start-up and shutdown race the window.
    expected = set(range(slots[1], slots[-1], INTERVAL))
    return {
        'occurrences': len(rows),
        'doubles': [r for r in rows if int(r[1]) > 1],
        'missing': sorted(expected - set(slots)),
        'replicas_seen': sorted({rep for r in rows for rep in r[2].split(',')}),
    }


def wait_for_occurrences(stack) -> None:
    stack.wait_for(
        f'SELECT 1 FROM poc_job_runs HAVING count(DISTINCT slot) >= {OCCURRENCES}',
        timeout=OCCURRENCES * INTERVAL + 90,
    )


def test_p1_once_per_occurrence(stack, record):
    s = stack(POC_INTERVAL_SECONDS=str(INTERVAL), POC_JOB_SECONDS='1')
    wait_for_occurrences(s)
    report = occurrence_report(s)
    record('P1', report)
    assert not report['doubles'], report
    assert not report['missing'], report


@pytest.mark.skipif(not MANIFEST.get('skew_service'), reason='single scheduler')
def test_p2_clock_skew(stack, record):
    # The skewed replica's clock runs 2 s ahead, so it fires 2 s early and its
    # 0.5 s job finishes before the other replica fires. A plain lock would
    # already be released; only a per-occurrence guard prevents a second run.
    s = stack(
        POC_INTERVAL_SECONDS=str(INTERVAL),
        POC_JOB_SECONDS='0.5',
        POC_FAKETIME_PRELOAD=FAKETIME,
        POC_SKEW='+2s',
    )
    wait_for_occurrences(s)
    report = occurrence_report(s)
    # A skewed scheduler that crashed leaves the other one scheduling alone,
    # which would pass for the wrong reason.
    running = s.compose('ps', '--status', 'running', '--services').split()
    # Replica clock minus database clock at start, per replica: evidence the skew applied.
    measured = dict(
        s.sql(
            'SELECT replica, round(avg(extract(epoch FROM fired_at - started_at))::numeric, 2) '
            'FROM poc_job_runs GROUP BY replica'
        )
    )
    record(
        'P2',
        {
            **report,
            'skewed_service': MANIFEST['skew_service'],
            'skew': '+2s',
            'measured_clock_offset_seconds': measured,
            'skewed_service_running': MANIFEST['skew_service'] in running,
        },
    )
    assert MANIFEST['skew_service'] in running, f'{MANIFEST["skew_service"]} crashed'
    assert not report['doubles'], report
    assert not report['missing'], report
