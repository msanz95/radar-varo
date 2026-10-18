import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar.core import due, economic_score, hot_window, ingest, initial_state, should_notify, validate
from radar.parser import SourceError, money, parse_product, parse_rendered, parse_card, confirm_detail
from radar.scan import permitted, rules_for

AT = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / 'fixtures'


def timestamp(days=0, minutes=0):
    return (AT + timedelta(days=days, minutes=minutes)).isoformat()


def product(sku='100001', price=1_000_000, **changes):
    p = {'sku': sku, 'name': 'Producto de prueba', 'brand': 'Fixture', 'url': f'https://www.telcel.com/tienda/producto/telefonos-y-smartphones/prueba/{sku}', 'surface': 'smartphones', 'category': 'Celulares', 'price_cents': price, 'listed_old_price_cents': None, 'promotion': '', 'availability': 'in_stock', 'variant': '128 GB · negro'}
    p.update(changes)
    return p


OK = [{'id': 'smartphones', 'status': 'OK', 'complete': True}]
FAIL = [{'id': 'smartphones', 'status': 'SOURCE_ERROR', 'complete': False, 'error': 'fixture unavailable'}]


class MemoryTests(unittest.TestCase):
    def test_real_changes_and_identical_scans_compact(self):
        s=ingest(initial_state(), [product()], OK, timestamp())
        s=ingest(s, [product()], OK, timestamp(minutes=15))
        self.assertEqual(len(s['history']['telcel:100001']), 1)
        self.assertEqual(s['history']['telcel:100001'][0]['count'], 2)
        self.assertEqual([e['type'] for e in s['events']], ['NEW_PRODUCT'])
        s=ingest(s, [product(price=600_000, promotion='Bono público', listed_old_price_cents=1_200_000)], OK, timestamp(minutes=30))
        self.assertTrue({'PRICE_DROP','PROMO_ON','LISTED_OLD_PRICE_CHANGE'}.issubset({e['type'] for e in s['events']}))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'], 'A')
        s=ingest(s, [product(price=700_000)], OK, timestamp(minutes=45))
        self.assertTrue({'PRICE_INCREASE','PROMO_OFF'}.issubset({e['type'] for e in s['events']}))

    def test_parser_failure_never_looks_like_disappearance(self):
        s=ingest(initial_state(), [product()], OK, timestamp())
        old_history=copy.deepcopy(s['history'])
        for i in range(1,6): s=ingest(s, [], FAIL, timestamp(minutes=i*15))
        self.assertTrue(s['products']['telcel:100001']['present'])
        self.assertEqual(s['products']['telcel:100001']['missing_count'], 0)
        self.assertEqual(s['history'], old_history)
        self.assertEqual(s['last_success'], timestamp())
        self.assertFalse(any(e['type']=='DISAPPEARED' for e in s['events']))

    def test_three_complete_absences_then_reappearance(self):
        s=ingest(initial_state(), [product()], OK, timestamp())
        for i in range(1,3):
            s=ingest(s, [], OK, timestamp(minutes=i*15))
            self.assertTrue(s['products']['telcel:100001']['present'])
        s=ingest(s, [], OK, timestamp(minutes=45))
        self.assertFalse(s['products']['telcel:100001']['present'])
        self.assertEqual(sum(e['type']=='DISAPPEARED' for e in s['events']), 1)
        s=ingest(s, [product(price=600_000)], OK, timestamp(minutes=60))
        self.assertTrue(s['products']['telcel:100001']['present'])
        self.assertEqual(s['products']['telcel:100001']['episode'], 1)
        self.assertEqual(sum(e['type']=='REAPPEARED' for e in s['events']), 1)

    def test_partial_surface_cannot_mark_missing(self):
        s=ingest(initial_state(), [product()], OK, timestamp())
        partial=[{'id':'smartphones','status':'OK','complete':False}]
        for i in range(4):s=ingest(s, [], partial, timestamp(minutes=15*(i+1)))
        self.assertEqual(s['products']['telcel:100001']['missing_count'], 0)

    def test_sku_variants_are_separate_and_duplicates_rejected(self):
        s=ingest(initial_state(),[product(),product('100002',price=1_100_000,variant='256 GB · negro')],OK,timestamp())
        self.assertEqual(len(s['products']),2)
        with self.assertRaises(ValueError):ingest(s,[product(),product()],OK,timestamp(minutes=15))

    def test_unknown_promotion_does_not_create_false_promo_off(self):
        s=ingest(initial_state(),[product(promotion='Bono')],OK,timestamp())
        s=ingest(s,[product(promotion=None)],OK,timestamp(minutes=15))
        self.assertFalse(any(e['type']=='PROMO_OFF' for e in s['events']))

    def test_ads_cannot_generate_s_levels(self):
        s=ingest(initial_state(),[product(price=100_000,listed_old_price_cents=10_000_000)],OK,timestamp())
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'WATCH')
        self.assertEqual(s['alerts'],[])

    def test_s_levels_need_time_and_observed_history(self):
        s=initial_state()
        for day in range(8):s=ingest(s,[product()],OK,timestamp(days=day))
        s=ingest(s,[product(price=150_000)],OK,timestamp(days=8))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'S++')
        s=ingest(s,[product(price=150_000)],OK,timestamp(days=8,minutes=15))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'S++')

    def test_unconfirmed_stock_limits_severity(self):
        s=initial_state()
        for day in range(8):s=ingest(s,[product()],OK,timestamp(days=day))
        s=ingest(s,[product(price=100_000,availability='unknown')],OK,timestamp(days=8))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'A')

    def test_sold_out_not_actionable(self):
        s=initial_state()
        for day in range(8):s=ingest(s,[product()],OK,timestamp(days=day))
        s=ingest(s,[product(price=100_000,availability='out_of_stock')],OK,timestamp(days=8))
        self.assertEqual(s['alerts'],[])

    def test_stale_source_error_expires_actions_but_preserves_history(self):
        s=ingest(initial_state(),[product()],OK,timestamp())
        s=ingest(s,[product(price=500_000)],OK,timestamp(minutes=15))
        self.assertEqual(len(s['alerts']),1)
        s=ingest(s,[],FAIL,timestamp(minutes=60))
        self.assertEqual(s['alerts'],[])
        self.assertEqual(len(s['history']['telcel:100001']),2)

    def test_delivery_deduplicates_identical_scans(self):
        s=initial_state()
        for day in range(8):s=ingest(s,[product()],OK,timestamp(days=day))
        s=ingest(s,[product(price=400_000)],OK,timestamp(days=8))
        p=s['products']['telcel:100001']
        self.assertTrue(should_notify(p,None))
        receipt={'price_cents':p['price_cents'],'level':p['anomaly']['level'],'episode':p['episode'],'confidence':p['anomaly']['confidence']}
        s=ingest(s,[product(price=400_000)],OK,timestamp(days=8,minutes=15))
        p=s['products']['telcel:100001']
        self.assertFalse(should_notify(p,receipt))
        p['price_cents']=380_000
        self.assertTrue(should_notify(p,receipt))
        p['price_cents']=400_000;p['episode']+=1
        self.assertTrue(should_notify(p,receipt))

    def test_invalid_observation_is_atomic(self):
        original=initial_state()
        with self.assertRaises(ValueError):ingest(original,[product(price=0)],OK,timestamp())
        self.assertEqual(original,initial_state())
        for price in [True,-100,10.5,'10000']:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):validate(product(price=price))


class ParserTests(unittest.TestCase):
    def test_observed_live_card_and_detail_agree(self):
        card={'url':'https://www.telcel.com/tienda/producto/telefonos-y-smartphones/apple-iphone-16e-negro-128gb/71001914','name':'iPhone 16e 128GB','prices':['$ 12,799.00'],'old':'$14,999.00','promotion':'Promoción','basis':'En Amigo Kit desde','brand':'APPLE | 16E 128'}
        p=parse_card(card,'smartphones')
        self.assertEqual(p['price_cents'],1279900)
        self.assertEqual(p['listed_old_price_cents'],1499900)
        self.assertEqual(p['price_basis'],'advertised_from')
        self.assertEqual(p['availability'],'unknown')
        exact=confirm_detail({'sku':'SKU: 71001914','prices':['$\n12,799.00'],'old':'$14,999.00','buy_enabled':True},p)
        self.assertEqual(exact['price_cents'],p['price_cents'])
        self.assertEqual(exact['price_basis'],'exact')
        self.assertEqual(exact['availability'],'in_stock')

    def test_from_price_cannot_generate_high_tier(self):
        s=initial_state()
        for day in range(8):s=ingest(s,[product()],OK,timestamp(days=day))
        s=ingest(s,[product(price=100_000,price_basis='advertised_from')],OK,timestamp(days=8))
        self.assertEqual(s['products']['telcel:100001']['anomaly']['level'],'A')

    def test_currency_cents_not_installments(self):
        self.assertEqual(money('$ 5,999.50'),599950)
        for bad in ['desde 99','1.234,56','0','NaN','100 o 200']:
            with self.assertRaises(SourceError):money(bad)

    def test_synthetic_jsonld_categories(self):
        for fixture in json.loads((FIXTURES/'products.json').read_text()):
            with self.subTest(category=fixture['category']):
                html='<script type="application/ld+json">'+json.dumps(fixture['jsonld'])+'</script>'
                p=parse_product(html,fixture['url'],fixture['surface'])
                self.assertEqual(p['category'],fixture['category'])
                self.assertEqual(p['price_cents'],fixture['price_cents'])
                self.assertEqual(p['sku'],fixture['jsonld']['sku'])

    def test_real_public_js_shell_fails_closed(self):
        with self.assertRaises(SourceError):parse_product((FIXTURES/'telcel-shell.html').read_text(),product()['url'],'smartphones')

    def test_aggregate_offers_cannot_be_misread_as_variant_price(self):
        fixture=json.loads((FIXTURES/'products.json').read_text())[0]
        fixture['jsonld']['offers']['@type']='AggregateOffer'
        with self.assertRaises(SourceError):parse_product('<script type="application/ld+json">'+json.dumps(fixture['jsonld'])+'</script>',fixture['url'],fixture['surface'])

    def test_rendered_candidate_ignores_monthly_payment(self):
        p=parse_rendered({'name':'HONOR Watch 4','text':'HONOR Watch 4\nSKU: 7008263\n$ 2,999.00\no en hasta 13 pagos de $230.69\nColor: negro','buy_enabled':True},'https://www.telcel.com/tienda/producto/vida-conectada/honor-watch-4-negro/7008263','connected')
        self.assertEqual(p['price_cents'],299900)
        self.assertEqual(p['category'],'Relojes')

    def test_rendered_ambiguous_prices_and_sku_mismatch_are_rejected(self):
        for text in ['SKU: 100001\n$ 999.00\n$ 799.00\no en hasta 12 pagos de $66.58','SKU: 100002\n$ 999.00']:
            with self.assertRaises(SourceError):parse_rendered({'name':'Test','text':text},product()['url'],'smartphones')

    def test_malicious_product_url_is_rejected(self):
        for url in ['https://www.telcel.com.evil.test/tienda/producto/a/1','javascript:alert(1)','https://bad@www.telcel.com/tienda/producto/a/1']:
            with self.assertRaises(ValueError):validate(product(url=url))


class SchedulingAndPoliciesTests(unittest.TestCase):
    def test_cdmx_hot_boundaries(self):
        self.assertFalse(hot_window('2026-10-12T07:59:00+00:00'))
        self.assertTrue(hot_window('2026-10-12T08:00:00+00:00'))
        self.assertTrue(hot_window('2026-10-12T10:59:00+00:00'))
        self.assertFalse(hot_window('2026-10-12T11:00:00+00:00'))

    def test_duplicate_delayed_workflow_is_skipped(self):
        self.assertFalse(due('2026-10-12T08:00:00+00:00','2026-10-12T08:01:00+00:00'))
        self.assertTrue(due('2026-10-12T08:00:00+00:00','2026-10-12T08:05:00+00:00'))
        self.assertFalse(due('2026-10-12T18:00:00+00:00','2026-10-12T18:05:00+00:00'))
        self.assertTrue(due('2026-10-12T18:00:00+00:00','2026-10-12T18:15:00+00:00'))

    def test_robot_wildcards_and_longest_allow(self):
        rules=rules_for('User-agent: GPTBot\nAllow: /\n\nUser-agent: *\nDisallow: /tienda/cart*\nDisallow: /tienda/login?*\nAllow: /tienda/login\nDisallow: /*undefined\n')
        self.assertFalse(permitted('https://www.telcel.com/tienda/cart/checkout',rules))
        self.assertFalse(permitted('https://www.telcel.com/tienda/login?test=1',rules))
        self.assertFalse(permitted('https://www.telcel.com/tienda/undefined',rules))
        self.assertTrue(permitted('https://www.telcel.com/tienda/login',rules))
        self.assertTrue(permitted(product()['url'],rules))
        self.assertFalse(permitted('https://example.com/product',rules))

    def test_roi_denominator_is_cost_not_sale(self):
        r=economic_score(180000,100000,5000,True,.9)
        self.assertEqual(r['roi_on_cost'],.75)
        self.assertEqual(r['level'],'S+')
        self.assertEqual(economic_score(180000,100000,5000,False,.9)['level'],'WATCH')


if __name__=='__main__':unittest.main()
