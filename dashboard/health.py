"""Live-status check for the admin dashboard's banner — names the SPECIFIC
reason the bot isn't running on real Gemini replies, rather than the admin
having to guess from a support ticket. Shown on every admin page via
routes.py's context processor.
"""
from integrations import gemini_client, s3_client
from dashboard import db


def check() -> dict:
    gemini_error = gemini_client.configuration_error()
    issues = []
    if gemini_error:
        issues.append(f"Gemini not configured: {gemini_error}. Replies are falling back to rule-based answers.")
    if not s3_client.is_configured():
        issues.append("S3 analytics logging not configured — events are only being written to a local file, not Redash.")

    # Secondary signal: what fraction of recent bot replies actually used
    # rule-based fallback (Gemini configured but the live API call itself
    # failed this turn) — a real, currently-happening degradation even
    # when configuration looks fine.
    recent = db.get_analytics()
    total = recent['total_turns'] or 0
    rule_based = next((s['count'] for s in recent['source_breakdown'] if s['source'] == 'rule_based'), 0)
    fallback_rate = round(100 * rule_based / total, 1) if total else 0.0
    if not gemini_error and total >= 10 and fallback_rate > 20:
        issues.append(f"{fallback_rate}% of recent replies fell back to rule-based answers despite Gemini being configured — the live API may be failing.")

    return {
        'live': not issues,
        'issues': issues,
        'fallback_rate': fallback_rate,
    }
