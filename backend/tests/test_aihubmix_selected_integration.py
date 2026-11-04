"""All eight selected candidates use native bodies, never activation by catalog."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, config, db, main, media_policy, storage
from app import aihubmix_extra_images as images, aihubmix_extra_videos as videos
from app import providers_aihubmix as provider

SELECTED = (catalog.NANO, catalog.GPT_IMAGE, *images.MODELS, *videos.MODELS)


class SelectedModelIntegrationTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        # This suite retains the pre-acceptance candidate boundary; newly
        # accepted modes have independent current-sample scope tests.
        original=(catalog.FLASH,catalog.FAST,catalog.QWEN,catalog.MINI)
        self.enterContext(patch.dict(catalog.SAMPLE_PROFILES,
            {mid:catalog.SAMPLE_PROFILES[mid] for mid in original},clear=True))
        self.enterContext(patch.dict(catalog.SAMPLE_DETAILS,
            {mid:catalog.SAMPLE_DETAILS[mid] for mid in original},clear=True))
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fixture-unused",
            "AIHUBMIX_USD_TO_CNY": "8", "AIHUBMIX_ENABLED_MODELS": ""}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.send = self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("network forbidden")))
        path = Path(__file__).resolve().parents[2] / 'docs/evidence/v0.11-八款模型扩充-后端-20261030/schemas.json'
        self.schemas = {row['id']: next(e['request']['schema'] for e in row['schema']['endpoints']
                if e.get('path') in ('/ai/v1/images/generations', '/ai/v1/videos'))
                for row in json.loads(path.read_text())['items']}

    def params(self, mid, mode):
        profile = next(row for row in catalog.CANDIDATE_PROFILES[mid] if mode in row['generation_mode'])
        return {**{key: value[0] for key,value in profile.items()}, 'prompt': 'one complete fictional cup'}

    def test_all_sixteen_native_bodies_use_reviewed_top_level_and_extra_fields(self):
        asset = self.asset()
        ref = storage.asset_data_url(asset)
        for mid in SELECTED:
            kind = catalog.MODELS[mid]['kind']
            schema = self.schemas[mid[len(catalog.PREFIX):]]
            for mode in ('text_to_'+kind, 'image_to_'+kind):
                with self.subTest(model=mid,mode=mode):
                    preparation={}
                    body=provider.build_body(kind,mid,self.params(mid,mode),ref,preparation=preparation)
                    self.assertEqual(body['model'],mid[len(catalog.PREFIX):])
                    self.assertLessEqual(set(body),set(schema['properties']))
                    self.assertLessEqual(set(schema['required']),set(body))
                    extra=schema['properties']['extra']
                    if extra.get('additionalProperties') is False:
                        self.assertLessEqual(set(body.get('extra',{})),set(extra['properties']))
                    if mode.startswith('text_'):
                        self.assertNotIn('image',body)
                        self.assertNotIn('frame_images',body)
                        self.assertFalse(preparation)
                    elif kind=='image':
                        self.assertEqual(body['image'],ref)
                    else:
                        prepared=body['frame_images'][0]['image_url']['url']
                        self.assertEqual(preparation['source_sha256'],hashlib.sha256(storage.asset_path(asset).read_bytes()).hexdigest())
                        expected,metadata=media_policy.prepare_reference(ref,'16:9')
                        self.assertEqual(prepared,expected)
                        self.assertEqual(preparation['prepared_sha256'],metadata['prepared_sha256'])
        self.send.assert_not_called()

    def test_missing_new_contract_fields_use_own_defaults_without_mutating_inputs(self):
        for mid in catalog.EXTENSIONS:
            with self.subTest(model=mid):
                kind=catalog.MODELS[mid]['kind']
                params={'prompt':'one cup'}
                self.assertEqual(config.validate_params_for_model(mid,kind,params),[])
                body=provider.build_body(kind,mid,params)
                self.assertEqual(params,{'prompt':'one cup'})
                if mid in videos.MODELS:
                    self.assertEqual(body['duration'],5)
                    self.assertEqual(body['resolution'],videos.DEFAULT_RESOLUTIONS[mid])
                else:
                    self.assertEqual(body['size'],'1024x1024')
        for mid in videos.MODELS:
            self.error('MODEL_CAPABILITY_MISMATCH',lambda: provider.build_body('video',mid,
                {'prompt':'cup','duration':5.0}))
            self.error('MODEL_CAPABILITY_MISMATCH',lambda: provider.build_body('video',mid,
                {'prompt':'cup','ratio':'9:16'}))
        self.send.assert_not_called()

    def test_candidates_cannot_be_enabled_by_key_dictionary_or_mixed_allowlist(self):
        for mid in SELECTED:
            with self.subTest(model=mid), patch.dict(config.MODEL_ACTIVATION,{mid:'verified'}):
                kind=catalog.MODELS[mid]['kind'];params=self.params(mid,'text_to_'+kind)
                self.assertTrue(catalog.validation_info(mid)['contract_verified'])
                self.assertFalse(catalog.validation_info(mid)['sample_generation_verified'])
                self.assertFalse(config.model_submittability(mid)['submittable'])
                self.assertEqual(config.public_model_capabilities(mid)['input_modes'],[])
                self.error('MODEL_NOT_VERIFIED',lambda:provider.media_create(kind,mid,params))
                with patch.dict(os.environ,{'AIHUBMIX_ENABLED_MODELS':catalog.FLASH+','+mid}):
                    self.assertFalse(catalog.enabled_models_config()['valid'])
                    self.assertFalse(config.model_submittability(catalog.FLASH)['submittable'])
                self.error('MODEL_NOT_VERIFIED',lambda:main.create_task(main.TaskIn(kind=kind,model_id=mid,params=params)))
        self.assertEqual(db.query('SELECT * FROM tasks'),[])
        self.send.assert_not_called()

    def test_public_metadata_does_not_claim_real_watermark_or_silent_acceptance(self):
        for mid in SELECTED:
            info=catalog.validation_info(mid)
            self.assertEqual(info['tested_output_policy_versions'],[])
            self.assertFalse(info['current_output_policy_sample_verified'])
        self.assertEqual(catalog.validation_info(videos.KLING_V3)['watermark_control'],'watermark_info_enabled_false')
        self.assertEqual(catalog.validation_info(images.FLUX)['watermark_control'],'not_exposed')
        h3=config.public_model_capabilities(videos.MINIMAX_H3)['vendor_capabilities']['limits']
        self.assertEqual(h3['audio_control'],'not_exposed')
        self.assertFalse(h3['native_silent_output_verified'])

    def test_mode_specific_prices_are_not_replaced_by_other_provider_or_zero(self):
        self.error('MODEL_PRICE_UNCONFIRMED',lambda:catalog.estimate_cost('image',images.FLUX,
            {'size':'1024x1024','generation_mode':'image_to_image'}))
        self.assertEqual(catalog.estimate_cost('image',images.FLUX,{'size':'1024x1024'}),.48)
        for mid in (catalog.NANO,catalog.GPT_IMAGE,images.WAN,videos.SEEDANCE_25,videos.KLING_V3):
            self.error('MODEL_PRICE_UNCONFIRMED',lambda:catalog.estimate_cost(catalog.MODELS[mid]['kind'],mid,{}))
        for mid in (videos.MINIMAX_H3,videos.WAN_3):
            price=catalog.estimate_cost('video',mid,self.params(mid,'image_to_video'))
            self.assertGreater(price,0)
        with patch.dict(os.environ,{'AIHUBMIX_USD_TO_CNY':'0'}):
            self.error('MODEL_PRICE_UNCONFIRMED',lambda:catalog.estimate_cost('image',images.FLUX,{}))


if __name__=='__main__':unittest.main()
