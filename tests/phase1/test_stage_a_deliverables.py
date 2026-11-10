import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestStageADeliverables(unittest.TestCase):
    """Automated verification suite for Phase 1 (Stage A) discovery & specification deliverables."""

    def setUp(self) -> None:
        self.current_state_md = REPO_ROOT / "docs" / "architecture" / "current-state.md"
        self.current_risks_md = REPO_ROOT / "docs" / "architecture" / "current-risks.md"
        self.feature_inventory_md = REPO_ROOT / "docs" / "product" / "feature-inventory.md"
        self.ui_register_md = REPO_ROOT / "docs" / "product" / "ui-action-register.md"
        self.ui_register_json = REPO_ROOT / "docs" / "product" / "ui-action-register.json"

    def test_01_all_stage_a_documents_exist_and_non_empty(self) -> None:
        for path in (
            self.current_state_md,
            self.current_risks_md,
            self.feature_inventory_md,
            self.ui_register_md,
            self.ui_register_json,
        ):
            self.assertTrue(path.exists(), f"Missing deliverable: {path}")
            self.assertGreater(path.stat().st_size, 1000, f"Deliverable too small / incomplete: {path}")

    def test_02_all_25_core_tables_mapped_in_current_state(self) -> None:
        content = self.current_state_md.read_text(encoding="utf-8")
        expected_tables = [
            "users", "plans", "discounts", "channels", "sources",
            "usage", "articles", "posted", "ch_tokens", "ai_models",
            "deeplinks", "kv_cache", "ai_cache", "ai_usage", "pub_journal",
            "settings", "support", "support_map", "plan_changes", "pay_requests",
            "notifications", "referrals", "wallet", "credit_log", "rate_limits",
        ]
        for table in expected_tables:
            self.assertIn(f"`{table}`", content, f"Database table `{table}` missing from current-state.md")

    def test_03_domain_adaptive_scientific_and_rich_message_requirements_documented(self) -> None:
        state_text = self.current_state_md.read_text(encoding="utf-8")
        feat_text = self.feature_inventory_md.read_text(encoding="utf-8")
        for keyword in ("tabular", "itemized", "sequential", "statistical", "sendRichMessage", "deeplinks"):
            self.assertIn(keyword, state_text, f"Keyword `{keyword}` missing from current-state.md")
        self.assertIn("FEAT-ENG-06", feat_text)
        self.assertIn("FEAT-ENG-07", feat_text)

    def test_04_official_status_labels_present_in_feature_inventory(self) -> None:
        feat_text = self.feature_inventory_md.read_text(encoding="utf-8")
        required_labels = [
            "IMPLEMENTED_AND_VERIFIED",
            "IMPLEMENTED_BUT_NOT_END_TO_END_VERIFIED",
            "PARTIAL",
            "DOCUMENTED_BUT_NOT_IMPLEMENTED",
            "PRESENT_BUT_UNSUPPORTED_OR_EXPERIMENTAL",
            "LEGACY_OR_DEPRECATED",
            "UNKNOWN_REQUIRES_INVESTIGATION",
        ]
        for label in required_labels:
            self.assertIn(label, feat_text, f"Official classification label `{label}` missing")

    def test_05_ui_action_register_completeness_and_14_point_schema(self) -> None:
        data = json.loads(self.ui_register_json.read_text(encoding="utf-8"))
        actions = data.get("actions", [])
        self.assertGreaterEqual(len(actions), 40, "Expected comprehensive action register")

        required_fields = [
            "action_id",
            "label_fa",
            "label_en",
            "trigger",
            "roles_allowed",
            "ownership_and_entitlement",
            "parent_screen",
            "destination_screen",
            "input_and_validation",
            "domain_operation",
            "db_entities",
            "external_side_effects",
            "success_feedback_and_next",
            "error_empty_busy_expired",
            "destructive",
            "confirmation_required",
            "automated_test",
        ]

        seen_ids = set()
        all_triggers = " | ".join(a["trigger"] for a in actions)

        for action in actions:
            for field in required_fields:
                self.assertIn(field, action, f"Action {action.get('action_id')} missing `{field}`")
                self.assertIsNotNone(action[field])

            act_id = action["action_id"]
            self.assertNotIn(act_id, seen_ids, f"Duplicate action_id: {act_id}")
            seen_ids.add(act_id)

            # Verify destructive actions require confirmation
            if action["destructive"]:
                self.assertNotEqual(
                    action["confirmation_required"].strip().lower(),
                    "none",
                    f"Destructive action {act_id} must specify confirmation policy",
                )

            # Verify superadmin routes only allow superadmin role
            if action["trigger"].startswith("s:"):
                self.assertEqual(
                    action["roles_allowed"],
                    ["superadmin"],
                    f"Superadmin route {act_id} must restrict roles_allowed to ['superadmin']",
                )

            # Verify concrete callback length <= 64 bytes
            for raw_part in action["trigger"].split("|"):
                part = raw_part.strip()
                if part.startswith("/"):
                    continue
                # Replace placeholders with realistic IDs (-1001234567890, 999999, Asia/Tehran, etc.)
                sample = re.sub(r"\[([^\]]+)\]", r"\1", part)
                sample = (
                    sample.replace("<channel_id>", "-1001234567890")
                    .replace("<ch_id>", "-1001234567890")
                    .replace("<plan_id>", "999")
                    .replace("<source_id>", "99999")
                    .replace("<id>", "999999")
                    .replace("<m_id>", "9999")
                    .replace("<uid>", "1234567890")
                    .replace("<idx>", "15")
                    .replace("<page>", "10")
                    .replace("<p>", "10")
                    .replace("<field>", "max_caption")
                    .replace("<sort>", "score")
                    .replace("<h>", "23")
                    .replace("<tz>", "Asia/Tehran")
                    .replace("<code>", "PROMO2026")
                    .replace("<lang>", "fa")
                    .replace("<key>", "welcome_msg")
                )
                self.assertLessEqual(
                    len(sample.encode("utf-8")),
                    64,
                    f"Callback sample `{sample}` for {act_id} exceeds Telegram 64-byte limit",
                )

        # Verify all mandatory commands and callback prefixes from Section 5 & Section 9 exist
        required_tokens = [
            "/start", "/lang", "/help", "/about", "/ref", "/man", "/cancel", "/create",
            "u:plans", "u:plan:", "u:free:", "u:req:", "u:disc:", "u:discx:", "u:ref",
            "u:man", "u:help", "u:lang", "u:pending:", "u:locked:", "u:freeused:", "u:home",
            "a:mand", "c:cancel",
            "a:home", "a:ch:", "a:chadd", "a:chdel:", "a:test:", "a:rep:", "a:tog:",
            "a:src:", "a:srca:", "a:srcv:", "a:srcr:", "a:srct:", "a:srctb:", "a:srcapi:", "a:srcapix:", "a:srcd:",
            "a:con:", "a:set:", "a:cat:", "a:catv:", "a:cata:", "a:cats:", "a:catd:",
            "a:cri:", "a:criv:", "a:cria:", "a:criw:", "a:crid:",
            "a:sch:", "a:quiet:", "a:tz:", "a:qs:", "a:qoff:", "a:tzs:", "a:mode:",
            "a:que:", "a:rej:", "a:pub:", "a:qreset:", "a:art:", "a:arte:", "a:artd:", "a:artf:", "a:artr:",
            "a:myai:", "a:myai_v:", "a:myai_t:", "a:myai_o:", "a:myai_e:", "a:myai_d:", "a:myai_add:", "a:plan",
            "s:report", "s:health", "s:plans", "s:discs", "s:users:", "s:admins:", "s:user:",
            "s:models", "s:pays", "s:mand", "s:texts", "s:bc", "s:auto", "s:ref",
            "s:plan:", "s:plan_new", "s:plan_e:", "s:plan_t:", "s:plan_free:", "s:plan_d:",
            "s:disc:", "s:disc_new", "s:disc_e:", "s:disc_t:", "s:disc_d:",
            "s:uplan:", "s:urevoke:", "s:uban:", "s:umsg:", "s:ureport:", "s:ufree:", "s:utests:", "s:uposts:",
            "s:model:", "s:model_add", "s:models_test", "s:model_test:", "s:model_t:", "s:model_e:", "s:model_d:", "s:model_test_pick:",
            "s:pay_rc:", "s:pay_ok:", "s:pay_no:", "s:pay_refund:",
            "s:mand_t:", "s:mand_e:", "s:txt:", "s:txtr:",
        ]
        for token in required_tokens:
            self.assertIn(token, all_triggers, f"Required trigger `{token}` missing from UI action register")


if __name__ == "__main__":
    unittest.main(verbosity=2)
