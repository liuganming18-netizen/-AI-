import json
import sqlite3
import time
from unittest.mock import patch
from tests.test_pr2_wo_02_fail_closed import FailClosedBase
import live_api as API
import live_store as L
import paper_engine as PE

class TestPaperEntry(FailClosedBase):
    def setUp(self):
        super().setUp()
        self.now = int(time.time() * 1000)
        self.uid = 'cand_http_valid'
        self.insert_signal_row(self.uid, event_time=self.now - 3000, confirmed_at=self.now - 2000, available_at=self.now - 1000)
        self.base = {'action': 'open', 'arm': '3a', 'symbol': 'PR2USDT', 'signal_uid': self.uid}

    def change(self, **values):
        con = sqlite3.connect(str(self.iso_live_db))
        try:
            con.execute('UPDATE live_signal SET ' + ','.join(k + '=?' for k in values) + ' WHERE signal_uid=?', (*values.values(), self.uid))
            con.commit()
        finally:
            con.close()

    def call(self, body=None):
        with patch.object(API, 'resolve_live_floor_ms', return_value=(self.now - 60000, None)), patch.object(PE, 'approved_params', return_value={'_config_valid': True, 'stoploss_s': .05}), patch.object(PE, 'open_position', return_value={'ok': False, 'reason': 'pending_future_fill'}) as opened:
            result, status = API.paper_order_post(self.base if body is None else body)
        return result, status, opened

    def reject(self, note):
        result, status, opened = self.call()
        self.assertEqual(status, 400, result)
        self.assertIn(note, result['quality']['note'])
        opened.assert_not_called()

    def test_research_replay_and_unknown_sources(self):
        for origin in ('historical_research', 'replay', 'test', 'other'):
            with self.subTest(origin=origin):
                self.change(origin=origin)
                self.reject('origin_not_actionable')

    def test_approved_state_role(self):
        self.change(payload_json=json.dumps({'arm': '3a', 'role': API.ARM_ENTRY_ROLE, 'approved': False}))
        self.reject('approved_false')
        self.change(payload_json=json.dumps({'arm': '3a', 'role': API.ARM_ENTRY_ROLE}), executable_state='blocked')
        self.reject('executable_state_not_enterable')
        self.change(executable_state='enterable', payload_json=json.dumps({'arm': '3a', 'role': 'other'}))
        self.reject('role_not_arm_entry')

    def test_test_uid(self):
        self.change(signal_uid='sig_closeloop_123')
        self.base['signal_uid'] = 'sig_closeloop_123'
        self.reject('test_signal_uid')

    def test_direction_and_arm(self):
        self.base['direction'] = 'short'
        self.reject('direction_mismatch_with_signal')
        del self.base['direction']
        self.change(payload_json=json.dumps({'arm': '3b', 'role': API.ARM_ENTRY_ROLE}))
        self.reject('arm_mismatch_with_signal')
        self.change(payload_json=json.dumps({'arm': '3a', 'role': API.ARM_ENTRY_ROLE}), direction='short')
        self.reject('direction_not_long')

    def test_missing_inverted_future_time(self):
        self.change(available_at=None)
        self.reject('available_at_missing')
        self.change(available_at=self.now - 4000)
        self.reject('confirmed_at_after_available_at')
        self.change(available_at=self.now + 60000)
        self.reject('available_at_after_read_at')

    def test_floor(self):
        with patch.object(API, 'resolve_live_floor_ms', return_value=(None, 'live_watermark_unavailable')), patch.object(PE, 'open_position') as opened:
            result, status = API.paper_order_post(self.base)
        self.assertEqual(status, 400)
        self.assertIn('live_watermark_unavailable', result['quality']['note'])
        opened.assert_not_called()
        self.change(event_time=self.now - 100000, confirmed_at=self.now - 100000, available_at=self.now - 100000)
        self.reject('before_effective_floor')

    def test_client_clock_and_replay_rejected(self):
        for extra in ({'now_ms': self.now - 100000}, {'requested_at': 1}, {'origin': 'replay'}, {'origin': 'test'}):
            with self.subTest(extra=extra):
                result, status, opened = self.call({**self.base, **extra})
                self.assertEqual(status, 400, result)
                opened.assert_not_called()

    def test_live_db_authority(self):
        result, status, opened = self.call({**self.base, 'origin': 'signal'})
        self.assertEqual(status, 200, result)
        kwargs = opened.call_args.kwargs
        self.assertEqual(kwargs['origin'], 'live')
        self.assertEqual(kwargs['event_time'], self.now - 3000)
        self.assertEqual(kwargs['available_at'], self.now - 1000)
        self.assertIsNone(kwargs['now_ms'])
        self.assertIsNone(kwargs['requested_at'])
        self.assertEqual(kwargs['direction'], 'long')

    def test_manual_cannot_reference_signal_or_unknown_uid(self):
        for body in ({**self.base, 'origin': 'manual'}, {**self.base, 'origin': 'manual', 'signal_uid': 'manual:123'}):
            result, status, opened = self.call(body)
            self.assertEqual(status, 400, result)
            opened.assert_not_called()

    def test_manual_server_uid(self):
        body = {'action': 'open', 'arm': '3a', 'symbol': 'PR2USDT', 'origin': 'manual'}
        result, status, opened = self.call(body)
        self.assertEqual(status, 200, result)
        kwargs = opened.call_args.kwargs
        self.assertRegex(kwargs['signal_uid'], r'^manual:[0-9a-f]{32}$')
        self.assertEqual(kwargs['origin'], 'manual')
        self.assertIsNone(kwargs['event_time'])

    def test_live_signal_real_pending(self):
        with patch.object(API, 'resolve_live_floor_ms', return_value=(self.now - 60000, None)), patch.object(L, 'effective_floor_ms', return_value=self.now - 60000):
            result, status = API.paper_order_post(self.base)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.pending_count(self.uid), 1)

    def test_manual_real_pending(self):
        body = {'action': 'open', 'arm': '3a', 'symbol': 'PR2USDT', 'origin': 'manual'}
        with patch.object(L, 'effective_floor_ms', return_value=self.now - 60000):
            result, status = API.paper_order_post(body)
        self.assertEqual(status, 200, result)
        uid = result['items'][0]['signal_uid']
        self.assertRegex(uid, r'^manual:[0-9a-f]{32}$')
        self.assertEqual(self.pending_count(uid), 1)

    def test_manual_retry_uid_and_collision(self):
        uid = 'manual:1700000000000'
        body = {'action': 'open', 'arm': '3a', 'symbol': 'PR2USDT', 'origin': 'manual', 'signal_uid': uid}
        result, status, opened = self.call(body)
        self.assertEqual(status, 200, result)
        self.assertEqual(opened.call_args.kwargs['signal_uid'], uid)
        self.insert_signal_row(uid, origin='historical_research')
        result, status, opened = self.call(body)
        self.assertEqual(status, 400, result)
        opened.assert_not_called()

    def test_approved_missing_and_null_match_existing_contract(self):
        for value in ({}, {'approved': None}, {'approved': True}):
            with self.subTest(value=value):
                self.change(payload_json=json.dumps({'arm': '3a', 'role': API.ARM_ENTRY_ROLE, **value}))
                result, status, opened = self.call()
                self.assertEqual(status, 200, result)
                opened.assert_called_once()
