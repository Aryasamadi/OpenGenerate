import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARCH = REPO / "docs" / "architecture"
ADR_DIR = ARCH / "decisions"

LEGACY_TABLES = [
    "users", "plans", "discounts", "channels", "sources", "usage", "articles", "posted",
    "ch_tokens", "ai_models", "deeplinks", "kv_cache", "ai_cache", "ai_usage", "pub_journal",
    "settings", "support", "support_map", "plan_changes", "pay_requests", "notifications",
    "referrals", "wallet", "credit_log", "rate_limits",
]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestStageBDeliverables(unittest.TestCase):
    def test_01_fourteen_adrs_with_required_sections(self) -> None:
        adrs = sorted(ADR_DIR.glob("0*.md"))
        self.assertEqual(len(adrs), 14)
        for adr in adrs:
            text = read(adr)
            self.assertRegex(text, r"\*\*Status:\*\* Accepted", adr.name)
            self.assertIn("## Decision", text, adr.name)
            self.assertTrue(
                "## Consequences" in text or "## Context" in text, f"{adr.name} lacks rationale"
            )
        # decisions that carry trade-offs must record rejected alternatives
        for n in ("0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0012", "0013"):
            f = next(ADR_DIR.glob(f"{n}-*.md"))
            self.assertIn("Alternatives rejected", read(f), f.name)

    def test_02_index_lists_every_adr_and_links_resolve(self) -> None:
        index = read(ADR_DIR / "README.md")
        for adr in ADR_DIR.glob("0*.md"):
            self.assertIn(adr.name, index)
        for md in list(ARCH.rglob("*.md")):
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", read(md)):
                if target.startswith(("http", "mailto")):
                    continue
                self.assertTrue((md.parent / target).exists(), f"{md.name}: broken link {target}")

    def test_03_decision_matrix_covers_all_stage_b_areas_and_criteria(self) -> None:
        m = read(ARCH / "decision-matrix.md")
        for crit in (
            "Correctness", "Reliability", "Performance", "Memory", "Operating cost",
            "Development velocity", "Maintainability", "Deployment complexity",
            "Ecosystem maturity", "Migration risk", "Future scaling",
        ):
            self.assertIn(crit, m)
        for area in ("D1.", "D2.", "D3.", "D4.", "D5.", "D6.", "D7.", "D8.", "D9.", "D10."):
            self.assertIn(area, m)
        line = m.split("Weights (sum = 100):")[1].split("\n")[0]
        weights = [int(x) for x in re.findall(r"\s(\d+)(?:,|\.)", line)]
        self.assertEqual(len(weights), 11)
        self.assertEqual(sum(weights), 100)

    def test_03b_matrix_totals_are_recomputed_not_asserted(self) -> None:
        m = read(ARCH / "decision-matrix.md")
        sections = re.split(r"^## ", m, flags=re.M)
        checked = 0
        for sec in sections:
            if not sec.startswith(("D1.", "D3.")):
                continue
            rows = [r for r in sec.splitlines() if re.match(r"\| .*\(\d+\)", r)]
            self.assertEqual(len(rows), 11, sec[:20])
            totals = None
            for r in rows:
                cells = [c.strip() for c in r.strip().strip("|").split("|")]
                weight = int(re.search(r"\((\d+)\)", cells[0]).group(1))
                scores = [int(re.match(r"\**(\d)", c).group(1)) for c in cells[1:]]
                totals = totals or [0] * len(scores)
                totals = [t + weight * s for t, s in zip(totals, scores)]
            declared = [int(x) for x in re.findall(r"\|\s*\*{0,2}(\d{3})\*{0,2}\s*(?=\|)",
                        next(r for r in sec.splitlines() if "Weighted total" in r))]
            self.assertEqual(totals, declared, sec[:20])
            checked += 1
        self.assertEqual(checked, 2)

    def test_04_core_choices_are_recorded_consistently(self) -> None:
        self.assertIn("PostgreSQL 16", read(ADR_DIR / "0003-database.md"))
        self.assertIn("Python 3.13", read(ADR_DIR / "0001-language-and-runtime.md"))
        self.assertIn("Rust gate", read(ADR_DIR / "0001-language-and-runtime.md"))
        self.assertIn("SKIP LOCKED", read(ADR_DIR / "0005-jobs-outbox-scheduler.md"))
        self.assertIn("pg_try_advisory_lock", read(ADR_DIR / "0005-jobs-outbox-scheduler.md"))
        money = read(ADR_DIR / "0004-money-and-ledger.md")
        for kw in ("BIGINT", "LEGACY", "idempotency", "Append-only", "refund"):
            self.assertIn(kw, money, kw)
        sec = read(ADR_DIR / "0009-security-baseline.md")
        for kw in ("DNS", "redirect", "MultiFernet", "LOCAL_LLM_ALLOWLIST", "audit_log"):
            self.assertIn(kw, sec, kw)

    def test_05_operator_content_requirement_is_in_the_design(self) -> None:
        t = read(ADR_DIR / "0008-content-formatting-and-rich-messages.md")
        for kw in ("tabular", "itemized", "sequential", "statistical", "narrative",
                   "analytical", "table", "details", "quote", "code", "RichAST",
                   "Lossless downgrade", "full"):
            self.assertIn(kw, t, kw)
        self.assertIn("proposes", t)  # model proposes, validator disposes

    def test_06_rich_message_claims_are_marked_unverified(self) -> None:
        t = read(ADR_DIR / "0006-telegram-transport.md")
        self.assertIn("not yet verified", t.replace("**", ""))
        self.assertIn("OI-6", read(ARCH / "target-blueprint.md"))

    def test_07_blueprint_completeness(self) -> None:
        b = read(ARCH / "target-blueprint.md")
        self.assertGreaterEqual(b.count("```mermaid"), 3)
        for kw in ("sequenceDiagram", "flowchart"):
            self.assertIn(kw, b)
        for tb in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"):
            self.assertIn(f"| {tb} |", b)
        for module in ("identity", "plans", "billing", "channels", "sources", "articles",
                       "ai", "media", "queue", "publishing", "support", "i18n",
                       "notifications", "admin", "health"):
            self.assertRegex(b, rf"`{module}`", module)
        for oi in range(1, 7):
            self.assertIn(f"OI-{oi}", b)
        self.assertIn("delivery_unknown", b)
        self.assertIn("Rust gate", b)

    def test_08_migration_plan_maps_all_legacy_tables_and_gates(self) -> None:
        mp = read(ARCH / "migration-plan.md")
        rows = re.findall(r"^\| \d+ \| `([a-z_]+)` \|", mp, flags=re.M)
        self.assertEqual(sorted(rows), sorted(LEGACY_TABLES))
        for g in range(1, 11):
            self.assertIn(f"| G{g} |", mp)
        for kw in ("never", "backup", "shadow", "Rollback", "LEGACY"):
            self.assertIn(kw, mp, kw)

    def test_09_phase1_docs_no_longer_claim_sqlite_primary(self) -> None:
        for p in (ARCH / "current-state.md", ARCH / "current-risks.md",
                  REPO / "docs" / "product" / "feature-inventory.md"):
            text = read(p)
            self.assertNotIn("BEGIN IMMEDIATE", text, p.name)
            self.assertNotIn("SQLite in WAL mode", text, p.name)
            self.assertNotIn("transactional SQLite (WAL)", text, p.name)
        self.assertIn("ADR-0003", read(ARCH / "current-state.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
