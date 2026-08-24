"""Multi-Channel Communication Dispatcher.

Delivers staff alert notifications across multiple channels:
- Slack (webhook)
- Twilio SMS
- EHR InBasket (HL7/FHIR integration)
- Claw3D WebSocket (3D UI real-time overlay)

In production, each channel handler would use the respective SDK/API.
Current implementation logs dispatches for demonstration.
"""

from app.models.triage import StaffAlertNotification


class MultiChannelDispatcher:
    """Dispatches agent notifications to Slack, SMS, WebSockets, and EHR systems."""

    async def dispatch_notifications(
        self, notifications: list[StaffAlertNotification]
    ) -> list[dict[str, str]]:
        """Send all notifications through their designated channels.

        Args:
            notifications: List of structured notification payloads.

        Returns:
            List of dispatch result dicts with status for each notification.
        """
        results: list[dict[str, str]] = []

        for note in notifications:
            if note.channel == "SLACK":
                result = await self._send_slack_alert(note)
            elif note.channel == "TWILIO_SMS":
                result = await self._send_sms_alert(note)
            elif note.channel == "CLAW3D_UI_WEBSOCKET":
                result = await self._send_websocket_alert(note)
            elif note.channel == "EHR_INBASKET":
                result = await self._send_ehr_inbasket(note)
            else:
                result = {"status": "UNSUPPORTED_CHANNEL", "channel": note.channel}

            results.append(result)

        return results

    async def _send_slack_alert(self, note: StaffAlertNotification) -> dict[str, str]:
        """Send alert to Slack channel via webhook."""
        print(
            f"[SLACK] [{note.priority}] To: {note.recipient_role}\n"
            f"  Title: {note.message_title}\n"
            f"  Body: {note.message_body}\n"
        )
        return {"status": "SENT", "channel": "SLACK", "recipient": note.recipient_role}

    async def _send_sms_alert(self, note: StaffAlertNotification) -> dict[str, str]:
        """Send SMS via Twilio API."""
        print(
            f"[TWILIO_SMS] [{note.priority}] To: {note.recipient_role}\n"
            f"  Message: {note.message_body}\n"
        )
        return {"status": "SENT", "channel": "TWILIO_SMS", "recipient": note.recipient_role}

    async def _send_websocket_alert(self, note: StaffAlertNotification) -> dict[str, str]:
        """Broadcast to Claw3D 3D UI via WebSocket."""
        print(
            f"[CLAW3D_UI_WEBSOCKET] [{note.priority}] To: {note.recipient_role}\n"
            f"  Title: {note.message_title}\n"
            f"  Action: {note.payload.get('action', 'N/A')}\n"
        )
        return {"status": "BROADCASTED", "channel": "CLAW3D_UI_WEBSOCKET", "recipient": note.recipient_role}

    async def _send_ehr_inbasket(self, note: StaffAlertNotification) -> dict[str, str]:
        """Send to EHR InBasket via HL7/FHIR integration."""
        print(
            f"[EHR_INBASKET] [{note.priority}] To: {note.recipient_role}\n"
            f"  Title: {note.message_title}\n"
            f"  Body: {note.message_body}\n"
        )
        return {"status": "DELIVERED", "channel": "EHR_INBASKET", "recipient": note.recipient_role}
