"""Wan/FLUX contracts against retained official Schema; zero network or DB."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import re
import unittest

from app import aihubmix_extra_images as images
from app.errors import AppError


class AiHubMixExtraImagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = Path(__file__).resolve().parents[2] / 'docs/evidence/v0.11-八款模型扩充-后端-20261030'
        rows = json.loads((evidence / 'schemas.json').read_text())['items']
        cls.schemas = {}
        cls.endpoints = {}
        for row in rows:
            mid = images.PREFIX + row['id']
            if mid not in images.MODELS:
                continue
            assert row['http'] == 200
            endpoint = next(ep for ep in row['schema']['endpoints']
                            if ep['path'] == '/ai/v1/images/generations')
            cls.endpoints[mid] = endpoint
            cls.schemas[mid] = endpoint['request']['schema']
        cls.prices = {row['model_id']: row for row in json.loads((evidence / 'prices.json').read_text())['items']}

    def params(self, mode='text_to_image', **changes):
        return {'generation_mode': mode, 'prompt': 'one complete fictional white cup',
                'size': '1024x1024', 'ratio': '1:1', 'watermark': False, **changes}

    def native_body(self, mid, params):
        body = {'model': mid[len(images.PREFIX):], 'prompt': params['prompt'],
                **images.body_options(mid, params)}
        if params['generation_mode'] == 'image_to_image':
            body['image'] = 'https://fixture.invalid/white-cup.png'
        return body

    def assert_error(self, code, fn):
        with self.assertRaises(AppError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_all_four_bodies_only_use_current_native_schema_fields(self):
        for mid in images.MODELS:
            endpoint = self.endpoints[mid]
            self.assertEqual(endpoint['method'], 'POST')
            self.assertTrue(endpoint['lifecycle']['supports_async'])
            schema = self.schemas[mid]
            allowed = schema['properties']
            for mode in ('text_to_image', 'image_to_image'):
                with self.subTest(model=mid, mode=mode):
                    body = self.native_body(mid, self.params(mode))
                    self.assertLessEqual(set(schema['required']), set(body))
                    self.assertLessEqual(set(body), set(allowed))
                    self.assertLessEqual(set(body.get('extra', {})), set(allowed['extra']['properties']))
                    self.assertEqual(body['n'], 1)
                    self.assertTrue(body['async'])
                    self.assertEqual(body['output_format'], 'png')
                    self.assertIn('png', allowed['output_format']['enum'])
                    self.assertEqual('image' in body, mode == 'image_to_image')
                    if 'maximum' in allowed['n']:
                        self.assertLessEqual(body['n'], allowed['n']['maximum'])
                    self.assertNotIn('aspect_ratio', body)  # size is the single sizing selector.

    def test_wan_square_size_satisfies_native_pixel_constraints(self):
        size_schema = self.schemas[images.WAN]['properties']['size']
        custom = next(row for row in size_schema['anyOf'] if row.get('x-sizing', {}).get('mode') == 'custom')
        self.assertRegex('1024x1024', custom['pattern'])
        constraints = custom['x-sizing']['constraints']['pixels']
        self.assertLessEqual(constraints['minimum'], 1024 * 1024)
        self.assertLessEqual(1024 * 1024, constraints['maximum'])
        flux_size = self.schemas[images.FLUX]['properties']['size']
        self.assertIsNotNone(re.fullmatch(flux_size['pattern'], '1024x1024'))

    def test_wan_reference_does_not_include_forbidden_thinking_or_sequence(self):
        schema = self.schemas[images.WAN]
        # Independent contract explicitly forbids thinking_mode when image input
        # is present; even thinking_mode=False would trigger this condition.
        thinking = next(rule for rule in schema['allOf']
                        if 'thinking_mode' in str(rule.get('if', {})))
        self.assertIn('not', thinking['then'])
        for mode in ('text_to_image', 'image_to_image'):
            body = self.native_body(images.WAN, self.params(mode))
            self.assertEqual(body['extra'], {'watermark': False, 'enable_sequential': False})
            self.assertNotIn('thinking_mode', body['extra'])
            self.assertNotIn('color_palette', body['extra'])
            self.assertNotIn('bbox_list', body['extra'])

    def test_flux_does_not_invent_a_watermark_parameter(self):
        extras = self.schemas[images.FLUX]['properties']['extra']['properties']
        self.assertNotIn('watermark', extras)
        self.assertEqual(images.WATERMARK_CONTROLS[images.FLUX], 'not_exposed')
        for mode in ('text_to_image', 'image_to_image'):
            body = self.native_body(images.FLUX, self.params(mode))
            self.assertEqual(body['extra'], {'prompt_upsampling': False})
            self.assertNotIn('watermark', body)
            self.assertNotIn('watermark', body['extra'])
            self.assertNotIn('seed', body)

    def test_invalid_or_unadapted_parameters_fail_before_body_creation(self):
        bad = [
            {'size': '2K'}, {'size': '2048x2048'}, {'size': {'width': 1024}}, {'size': None},
            {'ratio': '16:9'}, {'ratio': ['1:1']}, {'n': True}, {'n': 1.0}, {'n': 2},
            {'watermark': True}, {'watermark': 0}, {'watermark': None},
            {'seed': True}, {'seed': 42}, {'seed': '-1'}, {'seed': -2},
            {'generation_mode': 'image_to_video'}, {'generation_mode': {}},
            {'reference_images': 2}, {'reference_images': True}, {'reference_images': 1},
            {'input_asset_id': 'source'}, {'input_asset_id': ''}, {'input_asset_id': 3},
            {'prompt': None}, {'prompt': '   '},
            {'negative_prompt': 'blur'}, {'quality': 'max'}, {'resolution': '1K'},
            {'extra': {'watermark': False}}, {'images': ['reference']}, {'mask': 'mask'},
            {'async': True}, {'output_format': 'webp'}, {'thinking_mode': False},
            {'enable_sequential': False}, {'safety_tolerance': 6}, {'steps': 50},
        ]
        for mid in images.MODELS:
            for changes in bad:
                with self.subTest(model=mid, changes=changes):
                    self.assertTrue(images.parameter_problems(mid, self.params(**changes)))
                    self.assert_error('MODEL_CAPABILITY_MISMATCH',
                        lambda: images.body_options(mid, self.params(**changes)))
        self.assertTrue(images.parameter_problems(images.WAN, []))
        self.assert_error('MODEL_CAPABILITY_MISMATCH', lambda: images.body_options('unknown', self.params()))

    def test_reference_count_and_id_match_mode_without_loading_business_assets(self):
        for mid in images.MODELS:
            p = self.params('image_to_image', input_asset_id='fixture-image', reference_images=1)
            self.assertEqual(images.parameter_problems(mid, p), [])
            self.assertTrue(images.parameter_problems(mid, self.params('image_to_image', reference_images=0)))
            self.assertEqual(images.parameter_problems(mid, self.params(seed=-1)), [])
            self.assertEqual(images.parameter_problems(mid, self.params(seed=None)), [])
            original = deepcopy(p)
            images.body_options(mid, p)
            self.assertEqual(p, original)

    def test_profiles_and_capabilities_are_single_output_square_subsets_not_activation(self):
        for mid in images.MODELS:
            self.assertEqual(images.MODELS[mid]['kind'], 'image')
            profiles = images.PROFILES[mid]
            self.assertEqual({row['generation_mode'][0] for row in profiles}, {'text_to_image', 'image_to_image'})
            self.assertTrue(all(row['size'] == ['1024x1024'] and row['ratio'] == ['1:1'] for row in profiles))
            caps = images.CAPABILITIES[mid]
            self.assertEqual(caps['sizes'], ['1024x1024'])
            self.assertEqual(caps['max_reference_images'], 1)
            self.assertEqual(caps['limits']['max_output_images'], 1)
        self.assertFalse(hasattr(images, 'SAMPLE_PROFILES'))

    def test_flux_t2i_price_is_only_the_listed_output_megapixel_conservative_estimate(self):
        listed = self.prices['flux-2-pro']['pricing_lines']
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]['billable'], listed[0]['unit']), ('generate', 'megapixel'))
        self.assertEqual(Decimal(str(listed[0]['cost_usd'])), Decimal('0.03'))
        reserve = images.estimate_usd(images.FLUX, self.params())
        self.assertEqual(reserve, Decimal('0.06'))
        exact_output_cost = Decimal(1024 * 1024) / Decimal(1_000_000) * Decimal('0.03')
        self.assertGreaterEqual(reserve, exact_output_cost)
        self.assertEqual(images.estimate_usd(images.FLUX, {}), reserve)
        self.assertEqual(images.estimate_usd(images.FLUX, {'size': '1024x1024', 'reference_images': 0}), reserve)
        self.assertTrue(images.USD_PRICING[images.FLUX]['conservative_upper_estimate'])
        self.assertEqual(images.USD_PRICING[images.FLUX]['generation_modes'], ['text_to_image'])

    def test_unknown_input_or_token_charges_are_never_omitted_or_zero_priced(self):
        self.assertNotIn(images.WAN, images.USD_PRICING)
        for mode in ('text_to_image', 'image_to_image'):
            self.assert_error('MODEL_PRICE_UNCONFIRMED', lambda: images.estimate_usd(images.WAN, self.params(mode)))
        for marker in ({'generation_mode': 'image_to_image'}, {'input_asset_id': 'source'},
                       {'reference_images': 1}, {'reference_images': True}, {'image': 'reference'},
                       {'images': ['reference']}):
            with self.subTest(marker=marker):
                self.assert_error('MODEL_PRICE_UNCONFIRMED', lambda: images.estimate_usd(images.FLUX, marker))
        self.assert_error('MODEL_CAPABILITY_MISMATCH', lambda: images.estimate_usd(images.FLUX, self.params(size='2048x2048')))
        self.assert_error('MODEL_CAPABILITY_MISMATCH', lambda: images.estimate_usd('unknown', self.params()))


if __name__ == '__main__':
    unittest.main()
