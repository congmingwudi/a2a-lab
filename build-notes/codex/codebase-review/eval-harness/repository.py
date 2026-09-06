"""Execute the real dependency-free repository seams using temporary stores."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from engine import require


def execute(data, root):
    sys.path.insert(0, str(root / 'src')) if str(root / 'src') not in sys.path else None
    from interop.models import AgentRequest, AgentResponse
    from interop.trace import JsonlFileSink, SqliteSink, TraceEvent, TraceRecorder
    from observability.store import ObsStore

    operation = data['operation']
    require(operation in ('request', 'response', 'record', 'store'), 'unknown repository operation')
    if operation in ('request', 'response'):
        model = AgentRequest if operation == 'request' else AgentResponse
        try:
            result = model.from_dict(data['payload']).to_dict()
        except (ValueError, TypeError, KeyError) as exc:
            return {'accepted': False, 'exception': type(exc).__name__}
        return {'accepted': True, 'result': result}

    with tempfile.TemporaryDirectory(prefix='a2a-eval-') as directory:
        path = Path(directory)
        sink = SqliteSink(path / 'lab.db')
        recorder = TraceRecorder(sinks=[JsonlFileSink(path), sink])
        warning = io.StringIO()
        if data.get('broken_sink'):
            class BrokenSink:
                def emit(self, event):
                    raise OSError('synthetic sink outage')
            recorder.sinks.insert(0, BrokenSink())
        store = None
        try:
            with contextlib.redirect_stderr(warning):
                for event in data.get('hops', []):
                    recorder.record(TraceEvent(**event))
            archive = [json.loads(line) for file in sorted(path.glob('*.jsonl'))
                       for line in file.read_text(encoding='utf-8').splitlines()]
            persisted = []
            if (path / 'lab.db').exists():
                conn = sqlite3.connect(path / 'lab.db')
                try:
                    conn.row_factory = sqlite3.Row
                    for row in conn.execute('SELECT * FROM trace_events ORDER BY ts, hop_seq'):
                        entry = dict(row)
                        for key in ('request_payload_raw', 'response_payload_raw'):
                            entry[key] = json.loads(entry[key]) if entry[key] is not None else None
                        persisted.append(entry)
                finally:
                    conn.close()
            result = {'archive': archive, 'sqlite': persisted, 'sink_warning': bool(warning.getvalue())}
            if operation == 'store':
                store = ObsStore(path / 'lab.db')
                for session in data.get('sessions', []):
                    store.upsert_session(**session)
                for event in data.get('events', []):
                    store.upsert_event(**event)
                for harvest in data.get('harvest', []):
                    store.set_harvest_status(**harvest)
                result.update(summary=store.summary(), callers=store.session_callers(),
                              lab_traces=store.session_lab_traces(),
                              joins={ref: sorted(store.lab_traces_for(ref)) for ref in data.get('refs', [])})
                result['sessions'] = store.list_sessions(include_raw=True)
            return result
        finally:
            if store is not None:
                store.close()
            # SqliteSink currently exposes no public close() API.
            if sink._conn is not None:
                sink._conn.close()
