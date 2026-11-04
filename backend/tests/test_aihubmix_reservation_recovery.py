"""Frozen direct-task estimates and unknown remote costs; no real DB/network."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import test_aihubmix_activation as fixture
from app import aihubmix_models as catalog, config, db, main, media_policy, node_execution
from app import providers_aihubmix as provider


class AiHubMixReservationRecoveryTests(unittest.TestCase):
    setUp = fixture.AiHubMixActivationTests.setUp
    enable = fixture.AiHubMixActivationTests.enable
    video_params = fixture.AiHubMixActivationTests.video_params
    body = fixture.AiHubMixActivationTests.body
    error = fixture.AiHubMixActivationTests.error

    def direct_video(self):
        self.enable()
        return main.create_task(self.body(catalog.FAST, self.video_params()))

    def run_task(self, task):
        current = db.get("tasks", task["id"])
        return provider.run_media(task["id"], current["model_id"], db.loads(current["params_json"]), Mock())

    def succeeded(self, usage=None):
        return {"status": "succeeded", "usage": usage or {}, "output": []}

    def test_direct_frozen_estimate_survives_closed_allowlist_and_fx_and_binds_all_inputs(self):
        task = self.direct_video()
        saved = db.get("tasks", task["id"])
        reservation = db.loads(saved["usage_json"])["cost_reservation"]
        self.assertEqual(reservation["estimated_cost_cny"], 2.2)
        self.assertEqual(reservation["kind"], "video")
        self.assertEqual(reservation["model_id"], catalog.FAST)
        self.assertRegex(reservation["input_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("complete cup", saved["usage_json"])
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": "", "AIHUBMIX_USD_TO_CNY": "0"}), \
             patch.object(provider, "media_create") as create, \
             patch.object(provider, "media_query", return_value=self.succeeded()) as query, \
             patch.object(provider, "download_result", return_value=b"original-video") as download:
            db.update("tasks", task["id"], {"remote_task_id": "existing-direct"})
            result = self.run_task(task)
        create.assert_not_called()
        query.assert_called_once_with("video", "existing-direct")
        download.assert_called_once()
        self.assertEqual(result["cost_cny"], 2.2)
        self.assertEqual(result["usage"]["cost_basis"], "estimate")
        self.assertEqual(result["usage"]["cost_reservation"], reservation)
        altered_params = {**db.loads(saved["params_json"]), "prompt": "different cup"}
        for change in ({"params_json": db.dumps(altered_params)}, {"kind": "image"},
                       {"model_id": catalog.FLASH}, {"input_asset_id": "different-reference"}):
            with self.subTest(change=list(change)):
                with db.conn() as connection:
                    self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda:
                        node_execution._reserved_cost_in_tx(connection, {**saved, **change}))

    def test_legacy_unknown_cost_can_fetch_result_but_stays_unknown_and_blocks_new_post(self):
        task = self.direct_video()
        db.update("tasks", task["id"], {"remote_task_id": "legacy-direct", "usage_json": None})
        untrusted = {"cost_reservation": {"estimated_cost_cny": 0},
                     "cost_pending_verification": False, "cost_basis": "estimate", "total_tokens": 17}
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": "", "AIHUBMIX_USD_TO_CNY": "0"}), \
             patch.object(provider, "media_create") as create, \
             patch.object(provider, "media_query", return_value=self.succeeded(untrusted)), \
             patch.object(provider, "download_result", return_value=b"original-video"):
            result = self.run_task(task)
        create.assert_not_called()
        self.assertIsNone(result["cost_cny"])
        self.assertEqual(result["usage"]["cost_basis"], "unknown")
        self.assertTrue(result["usage"]["cost_pending_verification"])
        self.assertNotIn("cost_reservation", result["usage"])
        self.assertEqual(result["usage"]["total_tokens"], 17)
        pending = db.loads(db.get("tasks", task["id"])["usage_json"])
        self.assertEqual(pending["cost_basis"], "unknown")
        self.assertTrue(pending["cost_pending_verification"])
        # Match the worker's completion write: a result can succeed while its
        # local CNY charge remains unsettled, without freeing the budget.
        db.update("tasks", task["id"], {"status": "succeeded", "cost_cny": None,
                                       "usage_json": db.dumps(result["usage"])})
        with db.conn() as connection:
            self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda:
                node_execution._reserved_cost_in_tx(connection, db.get("tasks", task["id"])))
        with patch.object(provider, "media_create") as send:
            next_task = main.create_task(self.body(catalog.FLASH, {"prompt": "another cup"}))
            self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: self.run_task(next_task))
            send.assert_not_called()
        # Restoring FX does not silently reprice the legacy record on retry.
        with patch.object(provider, "media_create") as create, \
             patch.object(provider, "media_query", return_value=self.succeeded()), \
             patch.object(provider, "download_result", return_value=b"same-video"):
            resumed = self.run_task(task)
        create.assert_not_called()
        self.assertIsNone(resumed["cost_cny"])
        self.assertTrue(resumed["usage"]["cost_pending_verification"])

    def test_new_post_counts_other_reservations_and_keeps_trusted_metadata_on_success(self):
        task = self.direct_video()
        local = db.loads(db.get("tasks", task["id"])["usage_json"])
        reservation = local["cost_reservation"]
        local["retained_marker"] = "creation-context"
        db.update("tasks", task["id"], {"usage_json": db.dumps(local)})
        other = main.create_task(self.body(catalog.FLASH, {"prompt": "another cup"}))
        with patch.object(config, "BUDGET_CNY", 2.25), patch.object(provider, "media_create") as create:
            # 2.2 video + .1488 image > 2.25; checking only this image would pass.
            self.error("BUDGET_EXCEEDED", lambda: self.run_task(other))
            create.assert_not_called()
        db.update("tasks", other["id"], {"status": "failed"})
        submitted_policy = media_policy.specification("video")

        def query(kind, remote_id):
            persisted = db.loads(db.get("tasks", task["id"])["usage_json"])
            self.assertEqual(persisted["cost_reservation"], reservation)
            self.assertEqual(persisted["retained_marker"], "creation-context")
            return self.succeeded({"cost_reservation": {"estimated_cost_cny": 0},
                                   "cost_pending_verification": True, "output_policy": {"version": "untrusted"},
                                   "total_tokens": 23})

        with patch.object(provider, "media_create", return_value={"remote_task_id": "new-direct",
                "output_policy": submitted_policy, "prepared_reference": None}) as create, \
             patch.object(provider, "media_query", side_effect=query), \
             patch.object(provider, "download_result", return_value=b"new-video"):
            result = self.run_task(task)
        create.assert_called_once()
        self.assertEqual(result["cost_cny"], 2.2)
        self.assertEqual(result["usage"]["cost_reservation"], reservation)
        self.assertEqual(result["usage"]["retained_marker"], "creation-context")
        self.assertEqual(result["usage"]["output_policy"], submitted_policy)
        self.assertNotIn("cost_pending_verification", result["usage"])
        self.assertEqual(result["usage"]["total_tokens"], 23)


if __name__ == "__main__":
    unittest.main()
