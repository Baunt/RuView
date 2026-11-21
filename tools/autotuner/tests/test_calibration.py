import pytest

from autotuner.calibration import CalibrationError, calibrate


class Server:
    def __init__(self, fault=None):
        self.calls, self.started, self.fault, self.polls = [], False, fault, 0
        self.ident = {"boot_epoch": "boot-1", "session_id": "session-1", "binding_digest": "a"*64, "source_node_ids": [1]}
        self.finished = False

    def __call__(self, path, body=None):
        self.calls.append((path, body))
        if path == '/health':
            return {"source": "esp32", "processing": {"state": "live", "last_update_age_ms": 10}}
        if path == '/api/v1/nodes':
            return {"nodes": [{"node_id": 1, "csi_status": "active", "csi_last_seen_ms": 10}]}
        if path == '/api/v1/calibration/status':
            if not self.started:
                return {"status": "fresh" if self.fault == 'existing' else "none", "active": self.fault == 'existing'}
            self.polls += 1
            return {**self.ident, "session_id": 'other' if self.fault == 'identity' else 'session-1',
                    "status": 'fresh' if self.finished else 'collecting', "model_id": 'model-1' if self.finished else None,
                    "frame_count": 1000, "min_frames": 100,
                    "elapsed_s": 12 if self.polls > 1 else 2, "min_duration_s": 10,
                    "sequence_fault_node_ids": [1] if self.fault == 'sequence' else [], "missing_source_node_ids": []}
        if path.startswith('/api/v1/calibration/start?'):
            self.started = True
            return {**self.ident, "success": True}
        if path == '/api/v1/calibration/stop':
            assert body == self.ident
            self.finished = True
            receipt = {**self.ident, "model_id": 'bad' if self.fault == 'receipt' else 'model-1'}
            return {"success": True, "model_id": 'model-1', "model_receipt": receipt}
        if path == '/api/v1/calibration/cancel':
            assert body == self.ident
            return {"success": True}
        raise AssertionError(path)


def test_calibration_waits_for_duration_after_frame_gate_and_verifies_receipt():
    server = Server()
    events = []
    result = calibrate(server, 'a'*64, 1, confirmed_empty=True, emit=events.append, sleep=lambda _: None)
    assert result['success']
    assert len([event for event in events if event['event'] == 'progress']) == 2
    assert events[-1]['event'] == 'completed'
    assert not any(path.endswith('/cancel') for path, _ in server.calls)


@pytest.mark.parametrize('fault', ['identity', 'sequence', 'existing', 'receipt'])
def test_calibration_fails_without_weakening_server_gates(fault):
    server = Server(fault)
    with pytest.raises(CalibrationError):
        calibrate(server, 'a'*64, 1, confirmed_empty=True, sleep=lambda _: None)
    if fault in ('identity', 'sequence', 'existing'):
        assert not any(path.endswith('/stop') for path, _ in server.calls)
    if fault == 'identity':
        assert not any(path.endswith('/cancel') for path, _ in server.calls)
    if fault == 'sequence':
        assert any(path.endswith('/cancel') for path, _ in server.calls)


def test_calibration_requires_human_empty_room_confirmation():
    server = Server()
    with pytest.raises(CalibrationError):
        calibrate(server, 'a'*64, 1)
    assert server.calls == []


def test_default_deadline_allows_the_real_ten_minute_server_requirement():
    clock = [0]
    class TenMinuteServer(Server):
        def __call__(self, path, body=None):
            result = super().__call__(path, body)
            if path == '/api/v1/calibration/status' and self.started and not self.finished:
                result.update(frame_count=1000, elapsed_s=clock[0], min_duration_s=600)
            return result
    server = TenMinuteServer()
    def advance(seconds):
        clock[0] += seconds
    result = calibrate(server, 'a'*64, 1, confirmed_empty=True, sleep=advance, monotonic=lambda:clock[0])
    assert result['success'] and clock[0] >= 600


@pytest.mark.parametrize('previous_status', ['stale', 'expired'])
def test_completed_old_model_can_be_renewed_even_when_active_means_model_present(previous_status):
    class OldModelServer(Server):
        def __call__(self, path, body=None):
            if path == '/api/v1/calibration/status' and not self.started:
                self.calls.append((path, body))
                return {**self.ident, 'active': True, 'status': previous_status,
                        'model_id': 'old-model', 'model_receipt': {'model_id': 'old-model'}}
            return super().__call__(path, body)
    server = OldModelServer()
    events = []
    result = calibrate(server, 'a'*64, 1, confirmed_empty=True, emit=events.append, sleep=lambda _:None)
    assert result['success']
    previous = next(event for event in events if event['event'] == 'previous_calibration')
    assert previous['status']['model_id'] == 'old-model'
    assert previous['status']['status'] == previous_status
    assert not any(path.endswith('/cancel') for path, _ in server.calls)


@pytest.mark.parametrize('previous_status', ['fresh', 'collecting', 'uncalibrated', 'unknown'])
def test_renewal_never_replaces_fresh_or_unfinished_models(previous_status):
    class ProtectedServer(Server):
        def __call__(self, path, body=None):
            if path == '/api/v1/calibration/status' and not self.started:
                self.calls.append((path, body))
                return {'active': True, 'status': previous_status}
            return super().__call__(path, body)
    server = ProtectedServer()
    with pytest.raises(CalibrationError):
        calibrate(server, 'a'*64, 1, confirmed_empty=True, sleep=lambda _:None)
    assert not any(path.startswith('/api/v1/calibration/start') for path, _ in server.calls)


def test_leave_delay_is_before_capture_and_physical_source_is_rechecked():
    server = Server()
    events = []
    waits = []
    def wait(seconds):
        if seconds == 60:
            assert not server.started
            waits.append(seconds)
    calibrate(server, 'a'*64, 1, confirmed_empty=True, start_delay_s=60,
              emit=events.append, sleep=wait)
    assert waits == [60]
    assert events[0]['event'] == 'waiting_for_empty'
    before_start = [path for path, _ in server.calls[:next(i for i,(path,_) in enumerate(server.calls) if path.startswith('/api/v1/calibration/start'))]]
    assert before_start.count('/health') == 2


def test_no_capture_if_source_disappears_during_leave_delay():
    server = Server()
    offline = [False]
    def api(path, body=None):
        if path == '/health' and offline[0]:
            return {'source':'esp32:offline','processing':{'state':'offline'}}
        return server(path, body)
    with pytest.raises(CalibrationError):
        calibrate(api, 'a'*64, 1, confirmed_empty=True, start_delay_s=60,
                  sleep=lambda _:offline.__setitem__(0,True))
    assert not server.started


class RelocationServer(Server):
    def __init__(self, fault=None):
        super().__init__()
        self.relocation_fault = fault
        self.reset_done = False

    def __call__(self, path, body=None):
        if path == '/api/v1/calibration/status':
            self.calls.append((path, body))
            if self.reset_done and self.relocation_fault != 'reset_unverified':
                return {'status': 'none', 'active': False, 'model_id': None, 'session_id': None}
            return {**self.ident, 'status': 'collecting' if self.relocation_fault == 'collecting' else 'fresh',
                    'active': True, 'model_id': 'foreign' if self.relocation_fault == 'foreign' else 'old-model',
                    'model_receipt': {'model_id': 'old-model'}}
        if path == '/api/v1/calibration/reset':
            self.calls.append((path, body))
            assert body == self.ident
            self.reset_done = True
            return {'success': True}
        return super().__call__(path, body)


def test_confirmed_relocation_resets_only_expected_model_and_archives_its_status():
    from autotuner.calibration import reset_for_relocation
    server = RelocationServer()
    expected = {**server.ident, 'model_id': 'old-model'}
    events = []
    reset_for_relocation(server, expected, 'b'*64, 1, confirmed_empty=True,
                         confirmed_relocated=True, emit=events.append)
    assert server.reset_done
    assert events[0]['status']['model_receipt']['model_id'] == 'old-model'
    assert events[-1]['event'] == 'relocation_reset_verified'


@pytest.mark.parametrize('fault', ['foreign', 'collecting'])
def test_relocation_refuses_other_or_unfinished_models(fault):
    from autotuner.calibration import reset_for_relocation
    server = RelocationServer(fault)
    with pytest.raises(CalibrationError):
        reset_for_relocation(server, {**server.ident, 'model_id': 'old-model'}, 'b'*64, 1,
                             confirmed_empty=True, confirmed_relocated=True)
    assert not server.reset_done


@pytest.mark.parametrize('empty,moved,digest', [(False, True, 'b'*64), (True, False, 'b'*64), (True, True, 'a'*64)])
def test_relocation_needs_both_confirmations_and_a_new_binding(empty, moved, digest):
    from autotuner.calibration import reset_for_relocation
    server = RelocationServer()
    with pytest.raises(CalibrationError):
        reset_for_relocation(server, {**server.ident, 'model_id': 'old-model'}, digest, 1,
                             confirmed_empty=empty, confirmed_relocated=moved)
    assert not server.reset_done


def test_relocation_requires_reset_status_readback():
    from autotuner.calibration import reset_for_relocation
    server = RelocationServer('reset_unverified')
    with pytest.raises(CalibrationError, match='visszaolvasás'):
        reset_for_relocation(server, {**server.ident, 'model_id': 'old-model'}, 'b'*64, 1,
                             confirmed_empty=True, confirmed_relocated=True)
