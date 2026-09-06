"""Offline evidence graders. These are lab acceptance rubrics, not protocol SDKs."""

from __future__ import annotations

import json
import hashlib
import math
import re
import statistics
from pathlib import Path


class CaseError(ValueError):
    """Invalid case/evidence, distinct from a rubric violation."""


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def require(condition, message):
    if not condition:
        raise CaseError(message)


def obj(value):
    require(isinstance(value, dict), "expected an object")
    return value


def rows(value):
    require(isinstance(value, list), "expected an array")
    for row in value:
        obj(row)
    return value


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def decode(value):
    """Raw wiretap bodies may be JSON text or one SSE JSON-RPC response."""
    if not isinstance(value, str):
        return obj(value)
    if value.lstrip().startswith(('data:', 'event:')):
        messages = []
        for block in value.replace('\r\n', '\n').split('\n\n'):
            data = '\n'.join(line[5:].lstrip() for line in block.splitlines()
                             if line.startswith('data:'))
            if data and data != '[DONE]':
                messages.append(json.loads(data))
        require(len(messages) == 1, "supply one SSE RPC response per observation")
        return obj(messages[0])
    return obj(json.loads(value))


SECRET_KEYS = {'authorization', 'access_token', 'refresh_token', 'client_secret',
               'password', 'api_key', 'x-api-key', 'x-lab-token', 'user_token', 'id_token'}
SECRET_TEXT = re.compile(
    r'Bearer\s+(?!\[REDACTED)[A-Za-z0-9._~+/=-]{8,}'
    r'|\bsk-[A-Za-z0-9_-]*[A-Za-z0-9]{24,}'
    r'|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}'
)


def has_secret(value):
    if isinstance(value, dict):
        return any((key.lower() in SECRET_KEYS and val not in (None, '', '[REDACTED]'))
                   or has_secret(val) for key, val in value.items())
    if isinstance(value, list):
        return any(has_secret(v) for v in value)
    if isinstance(value, str):
        if SECRET_TEXT.search(value):
            return True
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return False
        if isinstance(parsed, (dict, list)):
            return has_secret(parsed)
    return False


def pointer(value, path):
    require(isinstance(path, str) and (not path or path.startswith('/')),
            "assertion paths must be JSON pointers")
    for token in path.split('/')[1:] if path else []:
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


OPS = {'eq', 'contains', 'not_contains', 'ge', 'le', 'length', 'no_secrets'}


def assertions(actual, checks):
    failures = set()
    for check in checks:
        try:
            value = pointer(actual, check['path'])
        except (KeyError, IndexError, ValueError, TypeError):
            failures.add('assert.' + check['name'])
            continue
        op, expected = check['op'], check.get('value')
        if op == 'eq':
            ok = type(value) is type(expected) and value == expected
        elif op in ('ge', 'le'):
            ok = number(value) and number(expected)
            ok = ok and (value >= expected if op == 'ge' else value <= expected)
        elif op == 'length':
            ok = isinstance(value, (dict, list, str)) and len(value) == expected
        elif op in ('contains', 'not_contains'):
            ok = isinstance(value, (str, list, dict))
            if ok:
                found = expected in value
                ok = found if op == 'contains' else not found
        else:
            ok = not has_secret(value)
        if not ok:
            failures.add('assert.' + check['name'])
    return failures


def trace(actual, policy, root):
    events = rows(actual['hops'])
    trace_id = actual['trace_id']
    require(nonempty(trace_id), 'trace_id must be nonempty')
    failures = set()
    if len(events) < policy.get('min_hops', 1):
        failures.add('trace.missing')
    keys = set()
    for event in events:
        if not all(nonempty(event.get(k)) for k in
                   ('trace_id', 'source', 'target', 'protocol', 'transport_detail')):
            failures.add('trace.shape')
        if event.get('trace_id') != trace_id:
            failures.add('trace.correlation')
        if event.get('status') not in policy.get('allowed_statuses', ['ok']):
            failures.add('trace.status')
        if not number(event.get('latency_ms')) or event['latency_ms'] < 0:
            failures.add('trace.latency')
        elif event['latency_ms'] > policy.get('max_hop_ms', math.inf):
            failures.add('trace.budget')
        if not number(event.get('ts')) or type(event.get('hop_seq')) is not int:
            failures.add('trace.shape')
        elif event['hop_seq'] < 0:
            failures.add('trace.shape')
        else:
            # Different processes can assign the same hop_seq; do not impose a
            # global order or sum nested/client+server durations.
            key = (event['trace_id'], event['hop_seq'], event['ts'],
                   event.get('source'), event.get('target'))
            if key in keys:
                failures.add('trace.duplicate')
            keys.add(key)
        if event.get('request_payload_raw') is None or event.get('response_payload_raw') is None:
            failures.add('trace.payload')
        if has_secret(event):
            failures.add('trace.secret')
    seen = {(e.get('source'), e.get('target'), e.get('protocol')) for e in events}
    if any(tuple(route) not in seen for route in policy.get('routes', [])):
        failures.add('trace.route')
    return failures, {'hops': len(events), 'protocols': sorted({e.get('protocol', '') for e in events})}


def protocol(actual, policy, root):
    request, response = decode(actual['request']), decode(actual['response'])
    kind = policy['protocol']
    failures = set()
    if 'http_status' in actual and (type(actual['http_status']) is not int
                                   or not 200 <= actual['http_status'] < 300):
        failures.add('protocol.http_status')
    text = None
    session = None
    trace_id = None
    if kind == 'rest':
        text, session = response.get('text'), response.get('session_id')
        trace_id = request.get('trace_id') or actual.get('headers', {}).get('x-trace-id')
        if not isinstance(request.get('message'), str):
            failures.add('protocol.request')
    else:
        if request.get('jsonrpc') != '2.0' or response.get('jsonrpc') != '2.0':
            failures.add('protocol.envelope')
        if (request.get('id') is None or type(request.get('id')) is not type(response.get('id'))
                or request.get('id') != response.get('id')):
            failures.add('protocol.rpc_id')
        if ('result' in response) == ('error' in response):
            failures.add('protocol.envelope')
        if 'error' in response:
            failures.add('protocol.error')
        params, result = obj(request.get('params', {})), obj(response.get('result', {}))
        if kind == 'mcp':
            if request.get('method') != 'tools/call' or params.get('name') != policy.get('tool', 'ask'):
                failures.add('protocol.method')
            args = obj(params.get('arguments', {}))
            trace_id = args.get('trace_id')
            if not isinstance(args.get('message'), str):
                failures.add('protocol.request')
            if result.get('isError'):
                failures.add('protocol.error')
            structured = result.get('structuredContent')
            if isinstance(structured, dict):
                body = structured
            else:
                content = rows(result.get('content', []))
                raw_text = '\n'.join(c['text'] for c in content
                                     if c.get('type') == 'text' and isinstance(c.get('text'), str))
                try:
                    body = json.loads(raw_text)
                except ValueError:
                    body = {'text': raw_text}
                if not isinstance(body, dict):
                    body = {'text': raw_text}
            text, session = body.get('text'), body.get('session_id')
        else:
            if request.get('method') not in ('message/send', 'SendMessage'):
                failures.add('protocol.method')
            message = obj(params.get('message', {}))
            trace_id = obj(message.get('metadata', {})).get('trace_id')
            task = obj(result.get('task', result))
            session = task.get('contextId', task.get('context_id'))
            state = obj(task.get('status', {})).get('state', '')
            if state not in ('completed', 'TASK_STATE_COMPLETED'):
                failures.add('protocol.state')
            text = '\n'.join(part['text'] for artifact in rows(task.get('artifacts', []))
                             for part in rows(artifact.get('parts', []))
                             if isinstance(part.get('text'), str))
            if not nonempty(task.get('id')):
                failures.add('protocol.task_id')
            if not any(nonempty(p.get('text')) for p in rows(message.get('parts', []))):
                failures.add('protocol.request')
    if not nonempty(text):
        failures.add('protocol.answer')
    if 'trace_id' in policy and trace_id != policy['trace_id']:
        failures.add('protocol.trace_id')
    if 'session_id' in policy and session != policy['session_id']:
        failures.add('protocol.session_id')
    if any(term.casefold() not in (text or '').casefold() for term in policy.get('contains', [])):
        failures.add('protocol.content')
    if has_secret(actual):
        failures.add('protocol.secret')
    return failures, {}


def experiment(actual, policy, root):
    failures, metrics = trace(actual, policy, root)
    text = actual.get('text')
    if not nonempty(text):
        failures.add('experiment.answer')
    text = (text or '').casefold()
    if any(t.casefold() not in text for t in policy.get('contains', [])):
        failures.add('experiment.content')
    if any(t.casefold() in text for t in policy.get('forbidden', [])):
        failures.add('experiment.forbidden')
    if actual.get('target') != policy['target']:
        failures.add('experiment.target')
    if not number(actual.get('elapsed_ms')) or actual['elapsed_ms'] < 0:
        failures.add('experiment.duration')
    elif actual['elapsed_ms'] > policy.get('max_elapsed_ms', math.inf):
        failures.add('experiment.budget')
    if 'roles' in policy:
        legs = rows(actual.get('legs', []))
        roles = [l.get('role') for l in legs]
        if len(roles) != len(set(roles)) or set(roles) != set(policy['roles']):
            failures.add('experiment.coverage')
        for leg in legs:
            if leg.get('ok') is True and not nonempty(leg.get('text')):
                failures.add('experiment.empty_leg')
            elif leg.get('ok') is False:
                if (not nonempty(leg.get('error'))
                        or not re.search(r'\[leg unavailable:[^\]]*\b'
                                         + re.escape(str(leg.get('role', '')).casefold())
                                         + r'\b[^\]]*\]', text)):
                    failures.add('experiment.hidden_failure')
                if not policy.get('allow_partial', False):
                    failures.add('experiment.failed_leg')
            elif leg.get('ok') is not True:
                failures.add('experiment.leg_shape')
            if leg.get('trace_id') != actual['trace_id']:
                failures.add('experiment.leg_trace')
    if 'max_cost_usd' in policy:
        cost = actual.get('cost_usd')
        if not number(cost) or cost < 0:
            failures.add('experiment.cost_unknown')
        elif cost > policy['max_cost_usd']:
            failures.add('experiment.cost_budget')
    return failures, metrics


STATES = {'submitted', 'working', 'input_required', 'auth_required',
          'completed', 'failed', 'canceled', 'rejected', 'not_found'}
TERMINAL = {'completed', 'failed', 'canceled', 'rejected'}


def task(actual, policy, root):
    snapshots = rows(actual['snapshots'])
    require(bool(snapshots), 'task evidence must contain snapshots')
    failures = set()
    previous, last_ms = None, -1
    for snap in snapshots:
        state = str(snap.get('state', '')).lower().removeprefix('task_state_').replace('-', '_')
        if state not in STATES:
            failures.add('task.state')
        if previous in TERMINAL and state != previous:
            failures.add('task.regression')
        if previous == 'working' and state == 'submitted':
            failures.add('task.regression')
        if state == 'not_found':
            if not number(snap.get('at_ms')) or snap['at_ms'] > policy.get('not_found_grace_ms', 0):
                failures.add('task.visibility')
        else:
            previous = state
        for key in ('task_id', 'trace_id', 'context_id'):
            if snap.get(key) != policy[key]:
                failures.add('task.identity')
        if not number(snap.get('at_ms')) or snap['at_ms'] < last_ms:
            failures.add('task.clock')
        else:
            last_ms = snap['at_ms']
    if previous != policy.get('final_state', 'completed'):
        failures.add('task.final')
    if previous == 'completed' and not nonempty(snapshots[-1].get('text')):
        failures.add('task.answer')
    submit = actual.get('submit_ms')
    if not number(submit) or submit < 0 or submit > policy['max_submit_ms']:
        failures.add('task.submit_budget')
    if last_ms > policy['max_elapsed_ms']:
        failures.add('task.deadline')
    if policy.get('require_nonblocking', True) and number(submit) and submit >= last_ms:
        failures.add('task.blocking')
    return failures, {'snapshots': len(snapshots)}


def observability(actual, policy, root):
    hops, sessions, events = rows(actual['hops']), rows(actual['sessions']), rows(actual['events'])
    require(number(actual['as_of']), 'as_of must be a finite epoch timestamp')
    failures = set()
    by_key = {(s['platform'], s['native_id']): s for s in sessions}
    if len(by_key) != len(sessions):
        failures.add('obs.duplicate_session')
    event_keys = [(e['platform'], e['event_id']) for e in events]
    if len(event_keys) != len(set(event_keys)):
        failures.add('obs.duplicate_event')
    if any((e['platform'], e['native_session_id']) not in by_key for e in events):
        failures.add('obs.orphan')
    required = set(policy['platforms'])
    joined = set()
    for platform in required:
        refs = {h.get('platform_ref') for h in hops
                if h.get('trace_id') == actual['trace_id']
                and h.get('target') in policy['platforms'][platform] and h.get('platform_ref')}
        ref_hit = any((platform, ref) in by_key for ref in refs)
        rider_hit = any(e['platform'] == platform
                        and (platform, e['native_session_id']) in by_key
                        and re.search(r'\blab-trace:\s*' + re.escape(actual['trace_id'])
                                      + r'(?![A-Za-z0-9_-])', str(e.get('raw_json', '')))
                        for e in events)
        if ref_hit or rider_hit:
            joined.add(platform)
    rate = len(joined) / len(required) if required else 0
    if rate < policy.get('min_join_rate', 1):
        failures.add('obs.join')
    harvests = rows(actual['harvest'])
    for platform in required:
        matching = [h for h in harvests if h.get('platform') == platform]
        if len(matching) != 1 or matching[0].get('status') != 'ok':
            failures.add('obs.harvest')
            continue
        at = matching[0].get('last_harvest_at')
        if not number(at) or not 0 <= actual['as_of'] - at <= policy['max_age_s']:
            failures.add('obs.stale')
    unknown_usage = 0
    for session in sessions:
        usage = session.get('usage')
        if usage is None:
            unknown_usage += 1
        elif not isinstance(usage, dict) or any(not number(v) or v < 0 for v in usage.values()):
            failures.add('obs.usage')
    if has_secret(actual):
        failures.add('obs.secret')
    return failures, {'joined_platforms': len(joined), 'required_platforms': len(required),
                      'join_rate': rate, 'sessions_with_unknown_usage': unknown_usage}


def local_artifact(root, ref):
    require(isinstance(ref, str), 'artifact refs must be relative file paths')
    filename, _, anchor = ref.partition('#')
    path = (root / filename).resolve()
    require(path.is_relative_to(root.resolve()), 'artifact path escapes repository')
    if not path.is_file():
        return None
    if anchor:
        text = path.read_text(encoding='utf-8')
        if re.fullmatch(r'L[1-9][0-9]*', anchor):
            if int(anchor[1:]) > len(text.splitlines()):
                return None
        else:
            headings = [re.sub(r'[^\w\- ]', '', line.lstrip('#').strip().lower()).replace(' ', '-')
                        for line in text.splitlines() if line.startswith('#')]
            if anchor not in headings:
                return None
    return path


def insight(actual, policy, root):
    failures = set()
    status = actual.get('status')
    require(status in ('measured', 'observed', 'hypothesis'), 'unknown insight status')
    if status != policy['status']:
        failures.add('insight.status')
    refs = actual.get('refs', [])
    require(isinstance(refs, list), 'insight refs must be an array')
    if status != 'hypothesis' and (not refs or any(local_artifact(root, r) is None for r in refs)):
        failures.add('insight.reference')
    if status == 'hypothesis' and not nonempty(actual.get('test_plan')):
        failures.add('insight.test_plan')
    metrics = {}
    if status == 'measured':
        measurement = obj(actual.get('measurement', {}))
        if any(measurement.get(key) != policy[key] for key in ('metric', 'cohort', 'unit', 'statistic')
               if key in policy):
            failures.add('insight.measurement_contract')
        path = local_artifact(root, measurement.get('path', ''))
        if path is None:
            failures.add('insight.artifact')
            return failures, metrics
        samples = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        rows(samples)
        samples = [s for s in samples if s.get('metric') == measurement.get('metric')
                   and s.get('cohort') == measurement.get('cohort')]
        if len(samples) < policy.get('min_samples', 3):
            failures.add('insight.samples')
        if (any(not nonempty(s.get('trace_id')) for s in samples)
                or len({s.get('trace_id') for s in samples}) != len(samples)):
            failures.add('insight.provenance')
        if any(s.get('unit') != measurement.get('unit') for s in samples):
            failures.add('insight.unit')
        if not samples or any(not number(s.get('value')) or s['value'] < 0 for s in samples):
            failures.add('insight.values')
            return failures, metrics
        values = sorted(s['value'] for s in samples)
        stat = measurement.get('statistic')
        require(stat in ('p50', 'p95', 'mean'), 'unknown measurement statistic')
        computed = (statistics.median(values) if stat == 'p50' else
                    values[math.ceil(len(values) * .95) - 1] if stat == 'p95' else statistics.mean(values))
        claimed = measurement.get('value')
        if not number(claimed) or abs(claimed - computed) > policy.get('tolerance', 0):
            failures.add('insight.number')
        metrics = {'samples': len(samples), 'computed': computed,
                   'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return failures, metrics


GRADERS = {'trace': trace, 'protocol': protocol, 'experiment': experiment,
           'task': task, 'observability': observability, 'insight': insight}
POLICIES = {
    'trace': {'min_hops', 'allowed_statuses', 'max_hop_ms', 'routes'},
    'protocol': {'protocol', 'tool', 'trace_id', 'session_id', 'contains'},
    'experiment': {'min_hops', 'allowed_statuses', 'max_hop_ms', 'routes', 'target',
                   'contains', 'forbidden', 'max_elapsed_ms', 'roles', 'allow_partial', 'max_cost_usd'},
    'task': {'task_id', 'trace_id', 'context_id', 'not_found_grace_ms', 'final_state',
             'max_submit_ms', 'max_elapsed_ms', 'require_nonblocking'},
    'observability': {'platforms', 'min_join_rate', 'max_age_s'},
    'insight': {'status', 'metric', 'cohort', 'unit', 'statistic', 'min_samples', 'tolerance'},
    'repository': set(),
}
REQUIRED = {'protocol': {'protocol'}, 'experiment': {'target'},
            'task': {'task_id', 'trace_id', 'context_id', 'max_submit_ms', 'max_elapsed_ms'},
            'observability': {'platforms', 'max_age_s'}, 'insight': {'status'}}


def validate(case):
    obj(case)
    require(set(case) <= {'id', 'area', 'description', 'input', 'policy', 'checks',
                          'expected_violations', 'tags'}, 'unknown case field')
    require(all(k in case for k in ('id', 'area', 'description', 'input')), 'missing required case field')
    require(nonempty(case['id']) and re.fullmatch(r'[a-z0-9][a-z0-9._-]*', case['id']), 'invalid case id')
    require(nonempty(case['description']), 'description must be nonempty')
    area = case['area']
    require(area in POLICIES, 'unknown area')
    obj(case['input'])
    policy = obj(case.get('policy', {}))
    require(set(policy) <= POLICIES[area], 'unknown policy key')
    require(REQUIRED.get(area, set()) <= set(policy), 'missing required policy key')
    for key in ('min_hops', 'max_hop_ms', 'max_elapsed_ms', 'max_cost_usd', 'not_found_grace_ms',
                'max_submit_ms', 'max_age_s', 'min_samples', 'tolerance', 'min_join_rate'):
        if key in policy:
            require(number(policy[key]) and policy[key] >= 0, 'invalid numeric policy: ' + key)
    if 'min_join_rate' in policy:
        require(policy['min_join_rate'] <= 1, 'join rate must be at most 1')
    for key in ('min_hops', 'min_samples'):
        if key in policy:
            require(type(policy[key]) is int and policy[key] >= 1, key + ' must be a positive integer')
    for key in ('require_nonblocking', 'allow_partial'):
        if key in policy:
            require(type(policy[key]) is bool, key + ' must be boolean')
    if area == 'task':
        require(all(nonempty(policy[k]) for k in ('task_id', 'trace_id', 'context_id')),
                'task identity policy must contain strings')
        require(policy.get('final_state', 'completed') in STATES - {'not_found'}, 'invalid final_state')
    if area == 'experiment':
        require(nonempty(policy['target']), 'target must be nonempty')
    if area == 'protocol':
        require(policy['protocol'] in ('rest', 'mcp', 'a2a'), 'unknown protocol')
    if area == 'insight':
        require(policy['status'] in ('measured', 'observed', 'hypothesis'), 'unknown insight status policy')
        if policy['status'] == 'measured':
            require(all(nonempty(policy.get(k)) for k in ('metric', 'cohort', 'unit', 'statistic')),
                    'measured policy must pin metric, cohort, unit and statistic')
            require(policy['statistic'] in ('p50', 'p95', 'mean'), 'unknown statistic policy')
    if area == 'observability':
        require(bool(obj(policy['platforms'])), 'platform denominator must not be empty')
        require(all(isinstance(v, list) and v and all(nonempty(x) for x in v)
                    for v in policy['platforms'].values()), 'platform target lists must be nonempty')
    for key in ('contains', 'forbidden', 'roles', 'allowed_statuses'):
        if key in policy:
            require(isinstance(policy[key], list) and all(nonempty(x) for x in policy[key]),
                    'policy must contain strings: ' + key)
    if 'routes' in policy:
        require(isinstance(policy['routes'], list) and all(isinstance(r, list) and len(r) == 3
                and all(nonempty(x) for x in r) for r in policy['routes']), 'routes must be triples')
    expected = case.get('expected_violations', [])
    require(isinstance(expected, list) and all(nonempty(x) for x in expected)
            and len(expected) == len(set(expected)), 'invalid expected violations')
    require(isinstance(case.get('tags', []), list) and all(nonempty(x) for x in case.get('tags', [])),
            'tags must be strings')
    checks = rows(case.get('checks', []))
    names = set()
    for check in checks:
        require(set(check) <= {'name', 'path', 'op', 'value'}, 'unknown check field')
        require(nonempty(check.get('name')) and check['name'] not in names, 'invalid or duplicate check name')
        names.add(check['name'])
        require(check.get('op') in OPS, 'unknown assertion operator')
        require(isinstance(check.get('path'), str) and (check['path'] == '' or check['path'].startswith('/')),
                'invalid JSON pointer')
        require(check['op'] == 'no_secrets' or 'value' in check, 'assertion value required')
    if area == 'repository':
        require(bool(checks), 'repository cases require assertions')


def evaluate(case, actual, root):
    failures, metrics = (set(), {}) if case['area'] == 'repository' else GRADERS[case['area']](
        actual, case.get('policy', {}), root)
    failures |= assertions(actual, case.get('checks', []))
    return sorted(failures), metrics
