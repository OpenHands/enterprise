"""Opt-in measurements: run this file explicitly with pytest; see README."""

import asyncio
import json
import threading
import time

import httpx
import pytest


@pytest.mark.parametrize('trial', range(3))
def test_prepared_install(compose_proxy, trial, record_property):
    rig = compose_proxy
    rig.compose('up', '-d', 'automation')
    rig.automation = rig.service('automation')
    rig.wait_ready()
    elapsed = time.monotonic() - rig.install_started_at
    assert rig.client.get('/echo').json()['role'] == 'enterprise'
    assert rig.client.get('/api/automation/echo').json()['role'] == 'automation'
    record_property(
        'prepared_install',
        json.dumps(
            {
                'trial': trial,
                'both_services_ready_seconds': elapsed,
                'lifecycle_commands': 4,
                'scope': 'cached images; pre-generated config and certificates; excludes human preparation',
            }
        ),
    )


def test_proxy_exit_and_rollback(compose_proxy, record_property):
    rig = compose_proxy
    source = rig.candidate
    rig.compose('up', '-d', 'automation')
    rig.wait_ready()
    backends = {
        name: (rig.service(name).id, rig.service(name).attrs['State']['StartedAt'])
        for name in ('enterprise', 'automation')
    }
    original_url = str(rig.client.base_url)
    observations = []
    for target in ('caddy', 'nginx', 'haproxy'):
        if target == source:
            continue
        for destination, rollback in ((target, False), (source, True)):
            records = []
            stop = threading.Event()

            def probe(stop=stop, records=records):
                with httpx.Client(
                    verify=rig.tls,
                    trust_env=False,
                    timeout=0.5,
                    headers={'Connection': 'close'},
                ) as client:
                    while not stop.is_set():
                        started = time.monotonic()
                        try:
                            response = client.get(original_url + '/echo')
                            ok = (
                                response.status_code == 200
                                and response.json().get('role') == 'enterprise'
                            )
                        except (httpx.HTTPError, ValueError):
                            ok = False
                        records.append((started, time.monotonic(), ok))
                        stop.wait(0.05)

            thread = threading.Thread(target=probe)
            thread.start()
            try:
                time.sleep(0.3)
                began = time.monotonic()
                rig.swap_candidate(destination)
                elapsed = time.monotonic() - began
                assert str(rig.client.base_url) == original_url
                for name in backends:
                    current = rig.service(name)
                    assert (
                        current.id,
                        current.attrs['State']['StartedAt'],
                    ) == backends[name]
                for path, role in (
                    ('/echo?x=1&x=2', 'enterprise'),
                    ('/api/automation/echo?x=1&x=2', 'automation'),
                    ('/api/automation-other', 'enterprise'),
                ):
                    response = rig.client.get(path)
                    assert response.status_code == 200
                    assert response.json()['role'] == role
                    assert response.json()['scheme'] == 'https'
                time.sleep(0.3)
            finally:
                stop.set()
                thread.join(timeout=2)
            assert not thread.is_alive()
            successes = [r[1] for r in records if r[2]]
            assert len(successes) >= 2
            observations.append(
                {
                    'source': source if not rollback else target,
                    'target': destination,
                    'rollback': rollback,
                    'swap_seconds': elapsed,
                    'probe_count': len(records),
                    'failed_probes': sum(not r[2] for r in records),
                    'longest_success_gap_seconds': max(
                        b - a for a, b in zip(successes, successes[1:], strict=False)
                    ),
                    'backend_restarts': 0,
                    'application_edits': 0,
                    'public_url_changed': False,
                    'commands': 2,
                }
            )
    record_property('exit_measurements', json.dumps(observations))


def percentile(values, fraction):
    return sorted(values)[max(0, int(len(values) * fraction + 0.999999) - 1)]


async def traffic(rig, concurrency, seconds=15, automation_rps=None):
    deadline = time.monotonic() + seconds
    enterprise = []
    dispatch_lags = []
    automation = []
    async with httpx.AsyncClient(
        base_url=str(rig.client.base_url),
        verify=rig.tls,
        trust_env=False,
        timeout=7,
        limits=httpx.Limits(max_connections=160, max_keepalive_connections=160),
    ) as client:

        async def request(path, results):
            started = time.monotonic()
            try:
                response = await client.get(path)
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            results.append((time.monotonic() - started, status))

        async def worker():
            while time.monotonic() < deadline:
                await request('/api/automation/echo', automation)

        async def automation_producer():
            scheduled = time.monotonic()
            requests = []
            while scheduled < deadline:
                await asyncio.sleep(max(0, scheduled - time.monotonic()))
                requests.append(
                    asyncio.create_task(request('/api/automation/echo', automation))
                )
                scheduled += 1 / automation_rps
            await asyncio.gather(*requests)

        workers = (
            [asyncio.create_task(automation_producer())]
            if automation_rps
            else [asyncio.create_task(worker()) for _ in range(concurrency)]
        )
        scheduled = time.monotonic()
        requests = []
        while scheduled < deadline:
            await asyncio.sleep(max(0, scheduled - time.monotonic()))
            dispatch_lags.append(max(0, time.monotonic() - scheduled))
            requests.append(asyncio.create_task(request('/echo', enterprise)))
            scheduled += 0.05
        await asyncio.gather(*requests, *workers)
    assert enterprise
    return {
        'enterprise_requests': len(enterprise),
        'enterprise_errors': sum(s != 200 for _, s in enterprise),
        'enterprise_p50_ms': percentile([t * 1000 for t, _ in enterprise], 0.5),
        'enterprise_p95_ms': percentile([t * 1000 for t, _ in enterprise], 0.95),
        'enterprise_p99_ms': percentile([t * 1000 for t, _ in enterprise], 0.99),
        'dispatch_lag_p95_ms': percentile([t * 1000 for t in dispatch_lags], 0.95),
        'automation_requests': len(automation),
        'automation_statuses': {
            str(s): sum(status == s for _, status in automation)
            for s in set(status for _, status in automation)
        },
        'automation_concurrency': concurrency,
        'offered_automation_rps': automation_rps,
        'automation_p95_ms': percentile([t * 1000 for t, _ in automation], 0.95)
        if automation
        else None,
        'offered_enterprise_rps': 20,
        'offered_seconds': seconds,
    }


def test_bounded_contention_and_footprint(
    compose_proxy, docker_client, record_property
):
    rig = compose_proxy
    rig.compose('up', '-d', 'automation')
    rig.automation = rig.service('automation')
    rig.wait_ready()
    rig.container.update(
        cpu_period=100000, cpu_quota=100000, mem_limit='256m', memswap_limit='256m'
    )
    for name in ('enterprise', 'automation'):
        rig.service(name).update(
            cpu_period=100000, cpu_quota=50000, mem_limit='256m', memswap_limit='256m'
        )
    reports = []
    for mode, concurrency in (
        ('baseline', 0),
        ('busy_32', 32),
    ):
        if mode.startswith('paused'):
            rig.automation.pause()
        try:
            before = rig.container.stats(stream=False)
            started = time.monotonic()
            report = asyncio.run(traffic(rig, concurrency, seconds=10))
            after = rig.container.stats(stream=False)
            elapsed = time.monotonic() - started
            memory = after['memory_stats']
            report.update(
                {
                    'mode': mode,
                    'proxy_cpu_percent_one_core': 100
                    * (
                        after['cpu_stats']['cpu_usage']['total_usage']
                        - before['cpu_stats']['cpu_usage']['total_usage']
                    )
                    / (elapsed * 1e9),
                    'proxy_memory_usage_mib_end': memory['usage'] / 2**20,
                    'proxy_memory_working_set_mib_end': (
                        memory['usage']
                        - memory.get('stats', {}).get('inactive_file', 0)
                    )
                    / 2**20,
                }
            )
            reports.append(report)
        finally:
            if mode.startswith('paused'):
                rig.automation.unpause()
                rig.wait_ready()
    record_property('load_measurements', json.dumps(reports))
    record_property('proxy_image_bytes', rig.container.image.attrs['Size'])
    # Measurements are retained even when a criterion fails; no pass inference
    # from absence of a crash. These are proposed fixture SLOs, not product SLOs.
    baseline = reports[0]['enterprise_p95_ms']
    record_property(
        'provisional_containment',
        json.dumps(
            {
                r['mode']: r['enterprise_errors'] / r['enterprise_requests'] < 0.001
                and r['enterprise_p95_ms'] < max(20, 2 * baseline)
                for r in reports[1:]
            }
        ),
    )
    assert all(r['enterprise_requests'] >= 190 for r in reports)


def test_modest_fault_containment(compose_proxy, record_property):
    """Five automation requests/second: failure isolation, not capacity ranking."""
    rig = compose_proxy
    rig.compose('up', '-d', 'automation')
    rig.automation = rig.service('automation')
    rig.wait_ready()
    rig.container.update(
        cpu_period=100000, cpu_quota=100000, mem_limit='256m', memswap_limit='256m'
    )
    for name in ('enterprise', 'automation'):
        rig.service(name).update(
            cpu_period=100000, cpu_quota=50000, mem_limit='256m', memswap_limit='256m'
        )
    # Equalize first-response waits before comparing default and capped profiles.
    config = rig.config.read_text().replace(
        'response_header_timeout 2s', 'response_header_timeout 5s'
    )
    rig.write_config(config)
    rig.reload()
    rig.refresh_client()
    reports = []

    def measure(mode):
        report = asyncio.run(traffic(rig, 0, seconds=10, automation_rps=5))
        report['mode'] = mode
        reports.append(report)

    measure('healthy_uncapped')
    rig.automation.pause()
    try:
        measure('hung_uncapped')
    finally:
        rig.automation.unpause()
    rig.wait_ready()
    if rig.candidate == 'caddy':
        config = rig.config.read_text().replace(
            'reverse_proxy automation:8000 {',
            'reverse_proxy automation:8000 {\n      unhealthy_request_count 8',
        )
    elif rig.candidate == 'nginx':
        config = rig.config.read_text().replace(
            'server automation:8000 resolve;',
            'server automation:8000 resolve max_conns=8;',
        )
    else:
        config = (
            rig.config.read_text()
            .replace(
                'backend automation_backend\n',
                'backend automation_backend\n  timeout queue 100ms\n',
            )
            .replace(
                'server automation automation:8000 resolvers docker init-addr none',
                'server automation automation:8000 resolvers docker init-addr none maxconn 8 maxqueue 8',
            )
        )
    rig.write_config(config)
    validation = rig.validate()
    assert validation.exit_code == 0, validation.output.decode()
    rig.reload()
    rig.refresh_client()
    rig.wait_ready()
    measure('healthy_capped')
    rig.automation.pause()
    try:
        measure('hung_capped')
    finally:
        rig.automation.unpause()
    rig.wait_ready()
    rig.automation.kill()
    try:
        measure('crashed_capped')
    finally:
        rig.automation.start()
        rig.wait_ready()
    measure('recovered_capped')
    record_property('fault_measurements', json.dumps(reports))
    record_property(
        'capped_policy',
        '8 active automation slots; HAProxy additionally permits at most 8 queued requests with 100ms queue timeout; HTTP/1 fixture. Not identical queue semantics.',
    )
    assert all(r['enterprise_requests'] >= 190 for r in reports)
