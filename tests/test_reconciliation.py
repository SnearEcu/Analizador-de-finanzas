from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from app.db import Base, SessionLocal, engine
from app.models import Transfer
from app.services import build_fingerprint, get_statements, import_uploaded_statement, run_reconciliation


ROOT = Path(__file__).resolve().parent.parent


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.session = SessionLocal()
        for path in sorted(ROOT.glob("*.pdf")):
            import_uploaded_statement(self.session, path.name, path.read_bytes(), source_type="seed")
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_import_creates_three_statements(self):
        statements = get_statements(self.session)
        self.assertEqual(len(statements), 3)

    def test_reconciliation_matches_transfer_by_owner_amount_and_date(self):
        owner_id = next(
            movement.owner_id
            for movement in self.session.query(__import__("app.models", fromlist=["Movement"]).Movement).all()
            if movement.description_raw.startswith("*** SU PAGO")
        )
        transfer = Transfer(
            owner_id=owner_id,
            transfer_date=date(2026, 2, 18),
            description="Transferencia para pago tarjeta",
            amount=287.55,
            direction="outgoing",
            confidence=0.9,
            fingerprint=build_fingerprint(owner_id, "manual", date(2026, 2, 18), 287.55, "outgoing"),
            raw_payload={"source": "test"},
        )
        self.session.add(transfer)
        self.session.commit()
        result = run_reconciliation(self.session)
        self.session.commit()
        self.assertGreaterEqual(result["created_links"], 1)


if __name__ == "__main__":
    unittest.main()
