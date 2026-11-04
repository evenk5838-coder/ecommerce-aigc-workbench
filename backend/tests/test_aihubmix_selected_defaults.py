"""Application defaults use the selected native contract before old defaults.

Acceptance/enablement below exists only in this disposable fixture. It is not a
claim that real FLUX/H3/Wan media passed acceptance, and every transport is denied.
"""
import hashlib
import os
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, canvas, config, db, main, node_execution, storage
from app import aihubmix_extra_images as images, aihubmix_extra_videos as videos
from app import providers_aihubmix as provider


class SelectedApplicationDefaultsTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        accepted = {
            images.FLUX: [row for row in images.PROFILES[images.FLUX]
                          if row['generation_mode'] == ['text_to_image']],
            videos.MINIMAX_H3: videos.PROFILES[videos.MINIMAX_H3],
            videos.WAN_3: videos.PROFILES[videos.WAN_3],
        }
        self.enterContext(patch.dict(catalog.SAMPLE_PROFILES, accepted))
        self.enterContext(patch.dict(catalog.SAMPLE_DETAILS, {
            mid: {'tested_output_policy_version': 'fixture-only',
                  'limitations': ['test-local acceptance, not a real sample'],
                  'evidence': 'fixture-only'} for mid in accepted}))
        self.enterContext(patch.dict(os.environ, {'AIHUBMIX_API_KEY': 'fixture-unused',
            'AIHUBMIX_USD_TO_CNY': '8', 'AIHUBMIX_ENABLED_MODELS': ','.join(accepted)}))
        self.enterContext(patch.object(config, '_load_dotenv', return_value=None))
        self.send = self.enterContext(patch.object(provider, '_request', side_effect=AssertionError('network forbidden')))

    def direct(self, mid, mode, changes=None, reference=None):
        return main.TaskIn(kind=catalog.MODELS[mid]['kind'], model_id=mid,
            params={'prompt': 'one complete fictional cup', 'generation_mode': mode, **(changes or {})},
            input_asset_id=reference)

    def raw_node(self, node_id, mid, mode, changes=None, reference=None):
        # Write an otherwise-valid persisted node whose settings are still empty.
        # This exercises the service rather than canvas's earlier default fill.
        create_mode = 'text_to_image' if mid == images.FLUX and mode == 'image_to_image' else mode
        self.create_node(node_id, create_mode, model_id=mid, params={})
        row = db.query('SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?',
                       (self.project, node_id))[0]
        cfg = db.loads(row['config_json'])
        cfg['params'] = changes or {}
        cfg['generation_mode'] = mode
        if reference is not None:
            cfg['input_asset_id'] = reference
        elif mode.startswith('image_'):
            cfg.pop('input_asset_id', None)
        db.update('canvas_nodes', row['id'], {'config_json': db.dumps(cfg)})
        return row

    def assert_video_defaults(self, params, resolution):
        self.assertEqual((params['duration'], params['ratio'], params['resolution'], params['generate_audio']),
                         (5, '16:9', resolution, False))

    def test_direct_video_request_resolves_own_defaults_and_body_matches(self):
        ref = self.asset()
        for mid, resolution in ((videos.MINIMAX_H3, '768P'), (videos.WAN_3, '480p')):
            for mode in ('text_to_video', 'image_to_video'):
                with self.subTest(model=mid, mode=mode):
                    source = ref['id'] if mode.startswith('image_') else None
                    submitted = self.direct(mid, mode, reference=source)
                    resolved = main.resolve_task(submitted)
                    self.assert_video_defaults(resolved['params'], resolution)
                    self.assertNotIn('resolution', submitted.params)
                    self.assertEqual(resolved['input_asset_id'], source)
                    reference = storage.asset_data_url(ref) if source else None
                    body = provider.build_body('video', mid, resolved['params'], reference)
                    self.assertEqual((body['duration'], body['aspect_ratio'], body['resolution']),
                                     (5, '16:9', resolution))
                    self.assertEqual(bool(body.get('frame_images')), bool(source))
        self.assertFalse(db.query('SELECT * FROM tasks'))
        self.send.assert_not_called()

    def test_empty_node_quotes_freeze_the_same_video_defaults_and_reference(self):
        ref = self.asset()
        for index, (mid, resolution) in enumerate(((videos.MINIMAX_H3, '768P'), (videos.WAN_3, '480p'))):
            for mode in ('text_to_video', 'image_to_video'):
                with self.subTest(model=mid, mode=mode):
                    node_id = f'candidate-{index}-{mode}'
                    source = ref['id'] if mode.startswith('image_') else None
                    self.raw_node(node_id, mid, mode, reference=source)
                    quote = node_execution.quote_node(self.project, node_id)
                    frozen = quote['input']
                    self.assert_video_defaults(frozen['params'], resolution)
                    self.assertEqual(frozen['input_asset_id'], source)
                    self.assertGreater(quote['estimated_cost_cny'], 0)
                    if source:
                        expected = hashlib.sha256(storage.asset_path(ref).read_bytes()).hexdigest()
                        self.assertEqual(frozen['reference']['sha256'], expected)
                    body = provider.build_body('video', mid, frozen['params'],
                                               storage.asset_data_url(ref) if source else None)
                    self.assertEqual((body['duration'], body['aspect_ratio'], body['resolution']),
                                     (5, '16:9', resolution))
        self.assertFalse(db.query('SELECT * FROM tasks'))
        self.send.assert_not_called()

    def test_flux_direct_and_empty_node_keep_square_single_image_contract(self):
        resolved = main.resolve_task(self.direct(images.FLUX, 'text_to_image'))
        self.assertEqual((resolved['params']['size'], resolved['params']['ratio']), ('1024x1024', '1:1'))
        self.assertEqual(resolved['estimated_cost_cny'], .48)
        self.raw_node('flux-empty', images.FLUX, 'text_to_image')
        quote = node_execution.quote_node(self.project, 'flux-empty')
        self.assertEqual((quote['input']['params']['size'], quote['input']['params']['ratio']), ('1024x1024', '1:1'))
        self.assertEqual(quote['estimated_cost_cny'], .48)
        body = provider.build_body('image', images.FLUX, quote['input']['params'])
        self.assertEqual((body['size'], body['n']), ('1024x1024', 1))
        self.assertFalse(db.query('SELECT * FROM tasks'))
        self.send.assert_not_called()

    def test_missing_image_reference_is_not_replaced_or_downgraded(self):
        for index, mid in enumerate((videos.MINIMAX_H3, videos.WAN_3, images.FLUX)):
            mode = 'image_to_image' if mid == images.FLUX else 'image_to_video'
            self.error('REFERENCE_IMAGE_REQUIRED', lambda: main.resolve_task(self.direct(mid, mode)))
            self.raw_node('no-ref-'+str(index), mid, mode)
            self.error('REFERENCE_IMAGE_REQUIRED', lambda: node_execution.quote_node(self.project, 'no-ref-'+str(index)))
        self.assertFalse(db.query('SELECT * FROM tasks'))
        self.send.assert_not_called()

    def test_explicit_unsupported_settings_are_never_overwritten_by_defaults(self):
        cases = ((videos.MINIMAX_H3, {'resolution': '720p'}),
                 (videos.WAN_3, {'resolution': '720p'}),
                 (videos.WAN_3, {'ratio': '9:16'}),
                 (videos.MINIMAX_H3, {'duration': 10}),
                 (videos.MINIMAX_H3, {'duration': 5.0}),
                 (videos.WAN_3, {'generate_audio': True}))
        for index, (mid, change) in enumerate(cases):
            with self.subTest(model=mid, change=change):
                self.error('MODEL_CAPABILITY_MISMATCH', lambda: main.resolve_task(
                    self.direct(mid, 'text_to_video', change)))
                self.raw_node('bad-'+str(index), mid, 'text_to_video', change)
                self.error('MODEL_CAPABILITY_MISMATCH', lambda: node_execution.quote_node(self.project, 'bad-'+str(index)))
        self.assertFalse(db.query('SELECT * FROM tasks'))
        self.send.assert_not_called()

    def test_legacy_available_models_keep_their_existing_direct_defaults(self):
        enabled = (catalog.FLASH, catalog.QWEN, catalog.FAST, catalog.MINI)
        with patch.dict(os.environ, {'AIHUBMIX_ENABLED_MODELS': ','.join(enabled)}):
            for mid in (catalog.FLASH, catalog.QWEN):
                resolved = main.resolve_task(self.direct(mid, 'text_to_image'))
                self.assertEqual((resolved['params']['size'], resolved['params']['ratio']), ('1024x1024', '1:1'))
            resolved = main.resolve_task(self.direct(catalog.FAST, 'text_to_video'))
            self.assert_video_defaults(resolved['params'], '480p')
            # Mini's old missing 9:16/720p values still fail; the new-candidate
            # helper must not silently rewrite an explicit/legacy contract.
            ref = self.asset()['id']
            self.error('MODEL_CAPABILITY_MISMATCH', lambda: main.resolve_task(
                self.direct(catalog.MINI, 'image_to_video', reference=ref)))
        self.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
