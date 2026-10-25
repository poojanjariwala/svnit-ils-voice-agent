"""
backend/campaign_manager.py
─────────────────────────────────────────────
Async Bulk Campaign Manager for Outbound Telephony.

Supports:
1. Parallel worker pool for outbound dialing (e.g. 5-10 concurrent for trial, 50-100 for production)
2. Queue-based scheduling with rate-limit throttling
3. Integration with TRAI ComplianceChecker (DND check, consent check, legal calling hours)
4. Real-time campaign progress tracking (ETA, completed, failed, conversion rate)
─────────────────────────────────────────────
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from models import Campaign, OutboundCall, Business
from compliance import compliance_checker
from phone_service import place_call, to_e164

logger = logging.getLogger("voxagent.campaign_manager")


class BulkCampaignManager:
    """
    Manages asynchronous high-throughput bulk calling campaigns.
    """

    def __init__(self, max_concurrent_calls: int = 5, dial_delay_seconds: float = 2.0):
        self.max_concurrent_calls = max_concurrent_calls
        self.dial_delay_seconds = dial_delay_seconds
        self.stop_signals: Dict[str, bool] = {}

    def stop_campaign(self, campaign_id: str) -> None:
        """Signal an active campaign to halt gracefully."""
        self.stop_signals[campaign_id] = True
        logger.info(f"Campaign stop requested: {campaign_id}")

    async def run_campaign(
        self,
        campaign_id: str,
        business_id: str,
        answer_url_template: str,
        db_factory,
        status_callback_template: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes outbound calls for a campaign across a pool of concurrent workers.

        Args:
            campaign_id: The ID of the campaign to execute.
            business_id: The business entity owning the campaign.
            answer_url_template: Base URL or template for the bridge callback.
            db_factory: Callable returning a fresh SQLAlchemy Session.
        """
        self.stop_signals[campaign_id] = False
        queue: asyncio.Queue = asyncio.Queue()

        # 1. Fetch pending calls from DB
        with db_factory() as db:
            campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if not campaign:
                return {"error": "Campaign not found"}
            campaign.status = "running"
            db.commit()

            pending_calls = (
                db.query(OutboundCall)
                .filter(
                    OutboundCall.campaign_id == campaign_id,
                    OutboundCall.status == "pending",
                )
                .all()
            )

            for call in pending_calls:
                queue.put_nowait({
                    "id": call.id,
                    "caller_name": call.caller_name or "Customer",
                    "caller_number": call.caller_number,
                    "info": call.info or "",
                })

        total_queued = queue.qsize()
        logger.info(f"Loaded {total_queued} pending calls for campaign {campaign_id}")

        if total_queued == 0:
            with db_factory() as db:
                c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
                if c:
                    c.status = "completed"
                    db.commit()
            return {"campaign_id": campaign_id, "status": "completed", "total": 0}

        # 2. Worker definition
        async def worker(worker_id: int):
            while not queue.empty():
                if self.stop_signals.get(campaign_id, False):
                    logger.info(f"Worker {worker_id} stopping due to cancel signal.")
                    break

                try:
                    job = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                await self._process_call_job(
                    job,
                    business_id,
                    answer_url_template,
                    status_callback_template,
                    db_factory,
                )
                queue.task_done()
                await asyncio.sleep(self.dial_delay_seconds)

        # 3. Launch concurrent workers
        concurrency = min(self.max_concurrent_calls, total_queued)
        workers = [asyncio.create_task(worker(i)) for i in range(concurrency)]
        await asyncio.gather(*workers, return_exceptions=True)

        # 4. Finalize campaign state
        with db_factory() as db:
            c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if c:
                c.status = "stopped" if self.stop_signals.get(campaign_id, False) else "completed"
                db.commit()

        logger.info(f"Campaign {campaign_id} finished execution.")
        return {"campaign_id": campaign_id, "status": "completed", "total_queued": total_queued}

    async def _process_call_job(
        self,
        job: Dict[str, Any],
        business_id: str,
        answer_url_template: str,
        status_callback_template: Optional[str],
        db_factory,
    ) -> None:
        """Processes a single outbound call with compliance checks and failover dispatch."""
        call_id = job["id"]
        number = job["caller_number"]
        norm_number = to_e164(number)

        with db_factory() as db:
            oc = db.query(OutboundCall).filter(OutboundCall.id == call_id).first()
            if not oc:
                return

            # Compliance check (DND + Consent + Time Window)
            is_allowed, reason = compliance_checker.can_call_number(
                phone_number=norm_number,
                business_id=business_id,
                db=db,
            )

            if not is_allowed:
                logger.warning(f"Compliance check blocked call to {norm_number}: {reason}")
                oc.status = "failed"
                oc.detail = f"Compliance block: {reason}"
                db.commit()
                return

            # Mark calling
            oc.status = "calling"
            oc.called_at = datetime.now()
            db.commit()

        # Build dynamic answer URL
        answer_url = answer_url_template.format(outbound_id=call_id, business_id=business_id)
        status_url = (
            status_callback_template.format(outbound_id=call_id, business_id=business_id)
            if status_callback_template
            else None
        )

        # Dispatch the call via Twilio (blocking HTTP — keep it off the event loop)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None,
            place_call,
            norm_number,
            answer_url,
            None,
            status_url,
        )

        with db_factory() as db:
            oc = db.query(OutboundCall).filter(OutboundCall.id == call_id).first()
            if not oc:
                return
            if res.get("ok"):
                oc.call_uuid = res.get("call_uuid")
                oc.detail = res.get("detail", "Call started")
            else:
                oc.status = "failed"
                oc.detail = res.get("detail", "Dial failed")
            db.commit()

    @staticmethod
    def get_campaign_progress(campaign_id: str, db: Session) -> Dict[str, Any]:
        """Calculates live analytics, ETA, and progress percentage for a campaign."""
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            return {"error": "Campaign not found"}

        calls = db.query(OutboundCall).filter(OutboundCall.campaign_id == campaign_id).all()
        total = len(calls)
        completed = sum(1 for c in calls if c.status in ("done", "failed"))
        success = sum(1 for c in calls if c.status in ("calling", "done"))
        failed = sum(1 for c in calls if c.status == "failed")
        in_progress = sum(1 for c in calls if c.status == "calling")

        progress_pct = int((completed / total) * 100) if total > 0 else 0

        # Estimated Time Remaining (assuming average ~3 minutes per call turn)
        remaining = total - completed
        est_minutes = int((remaining / max(1, 5)) * 1.5)

        return {
            "campaign_id": campaign.id,
            "status": campaign.status,
            "total": total,
            "completed": completed,
            "successful": success,
            "failed": failed,
            "in_progress": in_progress,
            "progress_percent": progress_pct,
            "estimated_minutes_remaining": est_minutes,
        }


# Singleton manager instance
campaign_manager = BulkCampaignManager()
