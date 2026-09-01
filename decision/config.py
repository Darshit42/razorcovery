"""Tunable knobs for the decision layer. All stopping-rule limits live
here so they are code, not prose (CLAUDE.md hard rule)."""

# PRD §7: max outbound *voice call* attempts per customer.
MAX_ATTEMPTS = 2

# PRD §7: no calls outside this local-time window [start, end).
# 9am inclusive .. 7pm exclusive, evaluated in the customer's timezone.
CALL_WINDOW_START_HOUR = 9
CALL_WINDOW_END_HOUR = 19

# Amount thresholds (INR) for choosing intervention richness.
# At/above VOICE -> a call is worth the cost; at/above SMS -> an SMS
# nudge; below that -> just drop a retry link.
VOICE_MIN_AMOUNT_INR = 1500
SMS_MIN_AMOUNT_INR = 400
