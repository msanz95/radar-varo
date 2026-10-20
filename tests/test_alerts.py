import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.error import URLError

from radar.alerts import evaluate, prepare_outbox, should_deliver
from radar.core import hot_window, due, ingest, initial_state
from radar.delivery import send
from radar.parser import parse_card, SourceError
from test_radar import product, OK, FAIL, timestamp

TOPIC = 'test_only_channel_not_a_real_destination_0000'


class PersonalAlertTests(unittest.TestCase):
    def state(self, **changes):
        return ingest(initial_state(), [product(**changes)], OK, timestamp())

    def test_s21_screenshot_triggers_promotion_without_faking_history(self):
        s=self.state(sku='70019064',name='Galaxy S21 FE 5G 128GB',price=549900,
                     listed_old_price_cents=1599900,price_basis='advertised_from',availability='unknown')
        p=s['products']['telcel:70019064']
        self.assertEqual(p['anomaly']['level'],'WATCH')
        self.assertEqual(p['anomaly']['drop_pct'],0)
        self.assertEqual(p['signal']['label'],'PROMOCIÓN FUERTE')
        self.assertEqual(p['signal']['published_discount_pct'],65.6)
        self.assertFalse(p['signal']['confirmed'])
        self.assertTrue(s['alerts'])
        self.assertEqual(len(s['outbox']),1)

    def test_1799_first_observation_is_urgent_without_history(self):
        s=self.state(price=179900)
        p=s['products']['telcel:100001']
        self.assertEqual(p['signal']['priority'],5)
        self.assertTrue(p['signal']['confirmed'])
        self.assertEqual(p['anomaly']['level'],'WATCH')

    def test_1800_exact_is_excluded(self):
        self.assertEqual(self.state(price=180000)['alerts'],[])

    def test_crossing_threshold_creates_target_alert(self):
        s=self.state(price=199900)
        s=ingest(s,[product(price=179900)],OK,timestamp(minutes=15))
        self.assertEqual(s['products']['telcel:100001']['signal']['label'],'PRECIO OBJETIVO')

    def test_accessory_is_not_a_cheap_phone(self):
        self.assertEqual(self.state(price=50000,category='Audífonos',surface='accessories')['alerts'],[])
        self.assertEqual(self.state(price=50000,name='Funda para celular')['alerts'],[])

    def test_monthly_and_down_payment_are_excluded(self):
        for caption in ['13 mensualidades','Enganche con plan','Renta mensual de equipo']:
            with self.subTest(caption=caption):
                self.assertEqual(self.state(price=42300,price_caption=caption)['alerts'],[])

    def test_card_rejects_installment_string(self):
        with self.assertRaises(SourceError):
            parse_card({'url':product()['url'],'name':'Galaxy','prices':['13 pagos de $423.00']},'smartphones')

    def test_from_price_is_preliminary(self):
        p=self.state(price=150000,price_basis='advertised_from',availability='unknown')['products']['telcel:100001']
        self.assertEqual(p['signal']['priority'],4)
        self.assertEqual(p['signal']['status'],'PRELIMINAR')

    def test_sold_out_cheap_phone_has_no_alert(self):
        s=self.state(price=150000,availability='out_of_stock')
        self.assertEqual(s['alerts'],[])
        self.assertEqual(s['outbox'],{})

    def test_published_discount_requires_both_thresholds(self):
        self.assertEqual(self.state(price=50000,listed_old_price_cents=100000,category='Otros')['alerts'],[])
        self.assertTrue(self.state(price=200000,listed_old_price_cents=400000)['alerts'])

    def test_overlap_produces_one_alert_and_one_pending_message(self):
        s=self.state(price=150000,listed_old_price_cents=500000)
        self.assertEqual(len(s['alerts']),1)
        self.assertEqual(len(s['outbox']),1)
        self.assertEqual(len(s['products']['telcel:100001']['signal']['reasons']),2)

    def test_variants_keep_separate_identity(self):
        s=ingest(initial_state(),[product('100001',price=150000),product('100002',price=250000)],OK,timestamp())
        self.assertEqual([a['product_id'] for a in s['alerts']],['telcel:100001'])

    def test_history_drop_a_can_notify_without_s_grade(self):
        s=self.state(price=1000000)
        s=ingest(s,[product(price=700000)],OK,timestamp(minutes=15))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'A')
        self.assertEqual(len(s['outbox']),1)

    def test_deferred_surface_is_not_a_source_error(self):
        s=ingest(initial_state(),[],[{'id':'accessories','status':'DEFERRED','complete':False}],timestamp())
        self.assertFalse(any(e['type']=='SOURCE_ERROR' for e in s['events']))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.s=ingest(initial_state(),[product(price=150000)],OK,timestamp())

    def test_not_configured_preserves_outbox(self):
        report=send(self.s,'',at=timestamp())
        self.assertFalse(report['configured'])
        self.assertEqual(report['pending'],1)

    def test_no_duplicate_after_server_acceptance(self):
        calls=[]
        def transport(payload):calls.append(payload);return {'id':'accepted'}
        report=send(self.s,TOPIC,at=timestamp(),transport=transport)
        self.assertEqual(report['delivered'],1)
        self.assertFalse(report['device_verified'])
        self.s=ingest(self.s,[product(price=150000)],OK,timestamp(minutes=5))
        send(self.s,TOPIC,at=timestamp(minutes=5),transport=transport)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.s['outbox'],{})

    def test_confirmation_can_realert_but_downgrade_cannot(self):
        s=ingest(initial_state(),[product(price=150000,price_basis='advertised_from',availability='unknown')],OK,timestamp())
        send(s,TOPIC,at=timestamp(),transport=lambda _: {'id':'preliminary'})
        s=ingest(s,[product(price=150000)],OK,timestamp(minutes=5))
        self.assertEqual(len(s['outbox']),1)
        send(s,TOPIC,at=timestamp(minutes=5),transport=lambda _: {'id':'confirmed'})
        s=ingest(s,[product(price=150000,price_basis='advertised_from',availability='unknown')],OK,timestamp(minutes=10))
        self.assertEqual(s['outbox'],{})

    def test_failed_send_retries_and_persists(self):
        saved=[]
        def fail(_):raise URLError('test network failure')
        report=send(self.s,TOPIC,at=timestamp(),transport=fail,persist=lambda s,r:saved.append(copy.deepcopy(s)))
        self.assertEqual(report['errors'],1)
        self.assertEqual(len(saved),1)
        item=next(iter(self.s['outbox'].values()))
        self.assertEqual(item['attempts'],1)
        self.assertFalse(self.s['delivery'])
        report=send(self.s,TOPIC,at=timestamp(minutes=1),transport=lambda _:self.fail('backoff ignored'))
        self.assertEqual(report['delivered'],0)
        report=send(self.s,TOPIC,at=timestamp(minutes=5),transport=lambda _:{'id':'retry'})
        self.assertEqual(report['delivered'],1)

    def test_expired_queue_never_sends_old_price(self):
        report=send(self.s,TOPIC,at=timestamp(minutes=31),transport=lambda _:self.fail('stale alert sent'))
        self.assertEqual(report['pending'],0)

    def test_changed_price_replaces_pending_message(self):
        self.s=ingest(self.s,[product(price=250000)],OK,timestamp(minutes=5))
        self.assertEqual(self.s['outbox'],{})

    def test_topic_never_enters_public_state(self):
        import json
        send(self.s,TOPIC,at=timestamp(),transport=lambda _:{'id':'receipt'})
        self.assertNotIn(TOPIC,json.dumps(self.s))

    def test_missing_server_receipt_does_not_mark_delivered(self):
        report=send(self.s,TOPIC,at=timestamp(),transport=lambda _: {})
        self.assertEqual(report['errors'],1)
        self.assertEqual(self.s['delivery'],{})

    def test_new_price_below_threshold_can_realert(self):
        send(self.s,TOPIC,at=timestamp(),transport=lambda _:{'id':'receipt'})
        self.s=ingest(self.s,[product(price=140000)],OK,timestamp(minutes=5))
        self.assertEqual(len(self.s['outbox']),1)

    def test_invalid_topic_does_not_call_provider(self):
        report=send(self.s,'invalid/channel',at=timestamp(),transport=lambda _:self.fail('invalid topic'))
        self.assertFalse(report['configured'])
        self.assertEqual(report['errors'],1)


class AfternoonScheduleTests(unittest.TestCase):
    def test_afternoon_boundaries_in_mexico_city(self):
        for hour,minute,expected in [(13,59,False),(14,0,True),(17,59,True),(18,0,False)]:
            with self.subTest(hour=hour,minute=minute):
                self.assertEqual(hot_window(f'2026-10-20T{hour:02}:{minute:02}:00-06:00'),expected)

    def test_five_minute_guard_in_afternoon(self):
        self.assertTrue(due('2026-10-20T14:00:00-06:00','2026-10-20T14:05:00-06:00'))
        self.assertFalse(due('2026-10-20T14:00:00-06:00','2026-10-20T14:01:00-06:00'))

    def test_cron_windows_do_not_overlap_and_cover_requested_hours(self):
        from pathlib import Path
        text=(Path(__file__).parents[1]/'.github/workflows/radar.yml').read_text()
        self.assertIn("2-57/5 8-10,20-23",text)
        self.assertIn("2,17,32,47 0-7,11-19",text)


if __name__=='__main__':unittest.main()
