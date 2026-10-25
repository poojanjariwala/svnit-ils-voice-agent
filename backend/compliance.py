"""
backend/compliance.py
─────────────────────────────────────────────
TRAI (Telecom Regulatory Authority of India) & Telephony Compliance Engine.

Key Requirements:
1. DND (Do Not Disturb) Registry filtering
2. Customer Consent Management (Opt-in / Opt-out tracking)
3. Calling Time Restrictions (TRAI permits commercial calls only 08:00 to 21:00)
4. Mandatory Call Recording Disclosure (multilingual: Hindi, Gujarati, English)
─────────────────────────────────────────────
"""

import logging
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Session

from models import Base, Consent
from phone_service import to_e164

logger = logging.getLogger("voxagent.compliance")


DISCLOSURE_MESSAGES = {
    "hi": "नमस्ते जी, गुणवत्ता और प्रशिक्षण के उद्देश्य से आपकी यह कॉल रिकॉर्ड की जा सकती है।",
    "gu": "નમસ્તે જી, ગુણવત્તા અને તાલીમના હેતુથી આપનો આ કૉલ રેકોર્ડ થઈ શકે છે.",
    "en": "Hello ji, please note that this call may be recorded for quality and training purposes.",
}


class ComplianceChecker:
    """
    Validates outbound calls against telecom regulations and owner consent records.
    """

    def __init__(self):
        # In production, this can sync against the national NDNC registry API
        self.dnd_registry: set[str] = set()

    def add_dnd_numbers(self, numbers: list[str]) -> None:
        """Add numbers to the active DND list."""
        for num in numbers:
            norm = to_e164(num)
            if norm:
                self.dnd_registry.add(norm)

    def is_within_calling_hours(self, dt: Optional[datetime] = None) -> bool:
        """
        TRAI restricts telemarketing calls strictly between 08:00 and 21:00.
        """
        now = dt or datetime.now()
        hour = now.hour
        return 8 <= hour < 21

    def can_call_number(
        self,
        phone_number: str,
        business_id: Optional[str] = None,
        db: Optional[Session] = None,
        ignore_hours: bool = False
    ) -> Tuple[bool, str]:
        """
        Full compliance validation for an outbound call.
        Returns: (is_allowed: bool, reason: str)
        """
        norm_number = to_e164(phone_number)
        if not norm_number:
            return False, "Invalid phone number format"

        # 1. Calling hours check
        if not ignore_hours and not self.is_within_calling_hours():
            return False, "Outside permitted calling hours (08:00 - 21:00 TRAI window)"

        # 2. National / Local DND Registry check
        if norm_number in self.dnd_registry:
            return False, "Number is registered on Do Not Disturb (DND) list"

        # 3. Explicit consent check (if db session provided)
        if db and business_id:
            record = (
                db.query(Consent)
                .filter(
                    Consent.business_id == business_id,
                    Consent.phone_number == norm_number,
                )
                .first()
            )
            if record and not record.opted_in:
                return False, "Customer has previously opted out from calls"

        return True, "Call permitted"

    def get_recording_disclosure(self, language_code: str = "en") -> str:
        """Returns mandatory call recording disclosure message in caller language."""
        return DISCLOSURE_MESSAGES.get(language_code, DISCLOSURE_MESSAGES["en"])


# Global singleton instance
compliance_checker = ComplianceChecker()


def record_consent(
    db: Session,
    business_id: str,
    phone_number: str,
    opted_in: bool = True,
    source: str = "manual"
) -> Consent:
    """Record or update consent status for a customer."""
    import uuid
    norm_number = to_e164(phone_number)
    existing = (
        db.query(Consent)
        .filter(
            Consent.business_id == business_id,
            Consent.phone_number == norm_number,
        )
        .first()
    )

    if existing:
        existing.opted_in = opted_in
        if opted_in:
            existing.opted_in_date = datetime.utcnow()
            existing.opted_out_date = None
        else:
            existing.opted_out_date = datetime.utcnow()
        existing.source = source
        db.commit()
        db.refresh(existing)
        return existing

    consent = Consent(
        id=str(uuid.uuid4())[:12],
        business_id=business_id,
        phone_number=norm_number,
        opted_in=opted_in,
        opted_in_date=datetime.utcnow(),
        source=source,
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent
