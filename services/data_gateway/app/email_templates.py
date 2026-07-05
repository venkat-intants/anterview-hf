"""Branded, mobile-friendly, language-aware transactional email templates.

One place that turns ``(template_key, lang, context)`` into a rendered
``(subject, html, text)``. Everything the spec asks for on the presentation side
lives here:

  * Branding + consistent layout — every email is wrapped in ``_layout`` (logo
    wordmark, 600px centered card, footer, hidden preheader for inbox previews).
  * Mobile-friendly — table-based, max-width 600px, inline CSS (email clients
    strip <style>), fluid on small screens, large tap-target CTA button.
  * Actionable — ``_button`` renders a bulletproof CTA (reset password, open exam,
    join interview, set password) plus a copy-paste URL fallback.
  * EN / HI / TE — candidate-facing templates (welcome, verify, reset, exam,
    interview) ship localized copy; internal/security templates are EN. Unknown
    languages fall back to EN.

SECURITY: every interpolated value is HTML-escaped at the use site (candidate
names / job titles are user-supplied and must never inject markup), and URLs are
attribute-escaped. This mirrors the escaping the legacy inline emails did.
"""

from __future__ import annotations

import html as html_lib
from dataclasses import dataclass

from app.config import settings

# Day-1 languages; anything else falls back to English.
_SUPPORTED_LANGS = {"en", "hi", "te"}

# Brand accent — used for the wordmark + CTA button.
_BRAND_COLOR = "#4f46e5"
_BG = "#f4f4f7"
_CARD = "#ffffff"
_TEXT = "#1f2933"
_MUTED = "#6b7280"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _brand() -> str:
    return settings.email_from_name or "Intants AI Interview"


def _norm_lang(lang: str | None) -> str:
    code = (lang or "en").split("-")[0].lower()
    return code if code in _SUPPORTED_LANGS else "en"


def _loc(lang: str, table: dict[str, dict[str, str]]) -> dict[str, str]:
    """Pick the localized string row for ``lang`` with an English fallback."""
    return table.get(lang, table["en"])


def _esc(value: str | None) -> str:
    return html_lib.escape(value or "")


def _esc_attr(value: str | None) -> str:
    return html_lib.escape(value or "", quote=True)


def _button(href: str, label: str) -> str:
    """A large, bulletproof, single CTA button (inline-styled anchor)."""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'style="margin:28px 0;"><tr><td align="center" bgcolor="{_BRAND_COLOR}" '
        f'style="border-radius:8px;">'
        f'<a href="{_esc_attr(href)}" target="_blank" '
        f'style="display:inline-block;padding:14px 28px;font-size:16px;'
        f'font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;'
        f'background-color:{_BRAND_COLOR};">{_esc(label)}</a>'
        f"</td></tr></table>"
    )


def _fallback_link(intro: str, url: str) -> str:
    """The 'if the button doesn't work, paste this URL' affordance."""
    return (
        f'<p style="font-size:13px;color:{_MUTED};line-height:1.5;margin:0 0 4px;">'
        f"{_esc(intro)}</p>"
        f'<p style="font-size:13px;line-height:1.5;margin:0 0 8px;word-break:break-all;">'
        f'<a href="{_esc_attr(url)}" target="_blank" style="color:{_BRAND_COLOR};">'
        f"{_esc(url)}</a></p>"
    )


def _layout(inner_html: str, *, preheader: str = "", brand: str | None = None) -> str:
    """Wrap a template's inner HTML in the shared branded, responsive shell.

    ``brand`` overrides the wordmark + footer with a tenant company name (e.g.
    "Google", "CDPR"); when it differs from the platform brand a small
    "Powered by <platform>" line is added to the footer for honest attribution.
    None falls back to the platform brand.
    """
    platform = _brand()
    org = (brand or "").strip() or platform
    brand = _esc(org)
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{_esc(preheader)}</div>"
        if preheader
        else ""
    )
    year_brand = brand
    powered_by = (
        f'<p style="margin:6px 0 0;">Powered by {_esc(platform)}.</p>'
        if org != platform
        else ""
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f"<title>{brand}</title></head>"
        f'<body style="margin:0;padding:0;background:{_BG};">'
        f"{pre}"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_BG};padding:24px 12px;"><tr><td align="center">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="max-width:600px;width:100%;">'
        # Header / wordmark
        f'<tr><td style="padding:8px 8px 20px;">'
        f'<span style="font-size:20px;font-weight:700;color:{_BRAND_COLOR};">'
        f"{brand}</span></td></tr>"
        # Card
        f'<tr><td style="background:{_CARD};border-radius:12px;padding:32px 32px 24px;'
        f'border:1px solid #ececf1;font-family:-apple-system,BlinkMacSystemFont,'
        f"'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:{_TEXT};font-size:15px;"
        f'line-height:1.6;">'
        f"{inner_html}"
        f"</td></tr>"
        # Footer
        f'<tr><td style="padding:20px 8px;color:{_MUTED};font-size:12px;'
        f"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
        f'Arial,sans-serif;line-height:1.5;">'
        f"<p style=\"margin:0 0 4px;\">This is an automated message from {year_brand}.</p>"
        f'<p style="margin:0;">If you did not expect this email you can safely ignore it.</p>'
        f"{powered_by}"
        f"</td></tr>"
        f"</table></td></tr></table></body></html>"
    )


def _greeting(lang: str, name: str | None) -> str:
    hi = {"en": "Hi", "hi": "नमस्ते", "te": "నమస్తే"}[lang]
    who = _esc(name) if name else {"en": "there", "hi": "", "te": ""}[lang]
    return f"{hi} {who}".strip() + ","


def _p(text_html: str) -> str:
    return f'<p style="margin:0 0 14px;">{text_html}</p>'


# ===========================================================================
# Template builders — each returns (subject, inner_html, text, preheader)
# ===========================================================================


def _t_welcome(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    verify_url = ctx.get("verify_url")
    loc = _loc(lang, {
        "en": {
            "subject": f"Welcome to {_brand()}",
            "pre": "Your account is ready — confirm your email to get started.",
            "lead": f"Welcome aboard! Your {_esc(_brand())} account has been created.",
            "body": "You can now sign in, take AI interviews and exams, and track your results.",
            "verify_lead": "To confirm this is your email address, tap the button below:",
            "cta": "Confirm my email",
            "fallback": "Or paste this link into your browser:",
            "outro": "Good luck — we're glad to have you.",
        },
        "hi": {
            "subject": f"{_brand()} में आपका स्वागत है",
            "pre": "आपका खाता तैयार है — शुरू करने के लिए अपना ईमेल पुष्टि करें।",
            "lead": f"स्वागत है! आपका {_esc(_brand())} खाता बना दिया गया है।",
            "body": "अब आप साइन इन कर सकते हैं, एआई इंटरव्यू और परीक्षाएँ दे सकते हैं, और अपने परिणाम देख सकते हैं।",
            "verify_lead": "यह पुष्टि करने के लिए कि यह आपका ईमेल पता है, नीचे दिए गए बटन पर टैप करें:",
            "cta": "मेरा ईमेल पुष्टि करें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "outro": "शुभकामनाएँ — आपका साथ पाकर हमें खुशी है।",
        },
        "te": {
            "subject": f"{_brand()}కి స్వాగతం",
            "pre": "మీ ఖాతా సిద్ధంగా ఉంది — ప్రారంభించడానికి మీ ఇమెయిల్‌ను నిర్ధారించండి.",
            "lead": f"స్వాగతం! మీ {_esc(_brand())} ఖాతా సృష్టించబడింది.",
            "body": "ఇప్పుడు మీరు సైన్ ఇన్ చేయవచ్చు, AI ఇంటర్వ్యూలు, పరీక్షలు ఇవ్వవచ్చు మరియు మీ ఫలితాలను చూడవచ్చు.",
            "verify_lead": "ఇది మీ ఇమెయిల్ చిరునామా అని నిర్ధారించడానికి, దిగువ బటన్‌ను నొక్కండి:",
            "cta": "నా ఇమెయిల్‌ను నిర్ధారించండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "outro": "శుభాకాంక్షలు — మీరు మాతో ఉన్నందుకు సంతోషం.",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(loc["body"])
    text_lines = [_greeting(lang, name), "", loc["lead"], loc["body"]]
    if verify_url:
        inner += _p(loc["verify_lead"]) + _button(verify_url, loc["cta"])
        inner += _fallback_link(loc["fallback"], verify_url)
        text_lines += ["", loc["verify_lead"], verify_url]
    inner += _p(loc["outro"])
    text_lines += ["", loc["outro"]]
    return loc["subject"], inner, "\n".join(text_lines), loc["pre"]


def _t_email_verify(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    verify_url = ctx["verify_url"]
    loc = _loc(lang, {
        "en": {
            "subject": f"Confirm your email · {_brand()}",
            "pre": "Confirm your email address.",
            "lead": "Please confirm your email address to finish securing your account.",
            "cta": "Confirm my email",
            "fallback": "Or paste this link into your browser:",
            "expiry": "This link expires soon. If it has expired, you can request a new one from your profile.",
        },
        "hi": {
            "subject": f"अपना ईमेल पुष्टि करें · {_brand()}",
            "pre": "अपना ईमेल पता पुष्टि करें।",
            "lead": "अपने खाते को सुरक्षित करने के लिए कृपया अपना ईमेल पता पुष्टि करें।",
            "cta": "मेरा ईमेल पुष्टि करें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "expiry": "यह लिंक जल्द ही समाप्त हो जाएगा। यदि यह समाप्त हो गया है, तो आप अपनी प्रोफ़ाइल से नया अनुरोध कर सकते हैं।",
        },
        "te": {
            "subject": f"మీ ఇమెయిల్‌ను నిర్ధారించండి · {_brand()}",
            "pre": "మీ ఇమెయిల్ చిరునామాను నిర్ధారించండి.",
            "lead": "మీ ఖాతాను సురక్షితం చేయడానికి దయచేసి మీ ఇమెయిల్ చిరునామాను నిర్ధారించండి.",
            "cta": "నా ఇమెయిల్‌ను నిర్ధారించండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "expiry": "ఈ లింక్ త్వరలో గడువు ముగుస్తుంది. గడువు ముగిస్తే, మీ ప్రొఫైల్ నుండి కొత్తదాన్ని అభ్యర్థించవచ్చు.",
        },
    })
    inner = (
        _p(_greeting(lang, name)) + _p(loc["lead"])
        + _button(verify_url, loc["cta"])
        + _fallback_link(loc["fallback"], verify_url)
        + _p(f'<span style="color:{_MUTED};font-size:13px;">{loc["expiry"]}</span>')
    )
    text = "\n".join([_greeting(lang, name), "", loc["lead"], verify_url, "", loc["expiry"]])
    return loc["subject"], inner, text, loc["pre"]


def _t_password_reset(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    reset_url = ctx["reset_url"]
    ttl_hours = ctx.get("ttl_hours", settings.password_reset_ttl_hours)
    loc = _loc(lang, {
        "en": {
            "subject": f"Reset your password · {_brand()}",
            "pre": "Reset your password with this secure link.",
            "lead": "We received a request to reset your password. Tap the button below to choose a new one:",
            "cta": "Reset my password",
            "fallback": "Or paste this link into your browser:",
            "expiry": f"This link expires in {ttl_hours} hour(s) and can be used once.",
            "ignore": "If you didn't request this, ignore this email — your password stays unchanged.",
        },
        "hi": {
            "subject": f"अपना पासवर्ड रीसेट करें · {_brand()}",
            "pre": "इस सुरक्षित लिंक से अपना पासवर्ड रीसेट करें।",
            "lead": "हमें आपका पासवर्ड रीसेट करने का अनुरोध मिला। नया पासवर्ड चुनने के लिए नीचे दिए बटन पर टैप करें:",
            "cta": "पासवर्ड रीसेट करें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "expiry": f"यह लिंक {ttl_hours} घंटे में समाप्त हो जाएगा और एक बार ही उपयोग किया जा सकता है।",
            "ignore": "यदि आपने यह अनुरोध नहीं किया, तो इस ईमेल को अनदेखा करें — आपका पासवर्ड अपरिवर्तित रहेगा।",
        },
        "te": {
            "subject": f"మీ పాస్‌వర్డ్‌ను రీసెట్ చేయండి · {_brand()}",
            "pre": "ఈ సురక్షిత లింక్‌తో మీ పాస్‌వర్డ్‌ను రీసెట్ చేయండి.",
            "lead": "మీ పాస్‌వర్డ్‌ను రీసెట్ చేయమని అభ్యర్థన అందింది. కొత్తది ఎంచుకోవడానికి దిగువ బటన్‌ను నొక్కండి:",
            "cta": "నా పాస్‌వర్డ్‌ను రీసెట్ చేయండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "expiry": f"ఈ లింక్ {ttl_hours} గంట(ల)లో గడువు ముగుస్తుంది మరియు ఒకసారి మాత్రమే ఉపయోగించవచ్చు.",
            "ignore": "మీరు దీన్ని అభ్యర్థించకపోతే, ఈ ఇమెయిల్‌ను విస్మరించండి — మీ పాస్‌వర్డ్ మారదు.",
        },
    })
    inner = (
        _p(_greeting(lang, name)) + _p(loc["lead"])
        + _button(reset_url, loc["cta"])
        + _fallback_link(loc["fallback"], reset_url)
        + _p(f'<span style="color:{_MUTED};font-size:13px;">{loc["expiry"]}</span>')
        + _p(f'<span style="color:{_MUTED};font-size:13px;">{loc["ignore"]}</span>')
    )
    text = "\n".join(
        [_greeting(lang, name), "", loc["lead"], reset_url, "", loc["expiry"], loc["ignore"]]
    )
    return loc["subject"], inner, text, loc["pre"]


def _t_login_alert(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    # Security/notification email — EN only (precise security wording).
    name = ctx.get("name")
    when = ctx.get("when", "")
    device = ctx.get("device", "")
    reset_url = ctx.get("reset_url")
    subject = f"New sign-in to your {_brand()} account"
    lines = [
        _p(_greeting("en", name)),
        _p(f"We noticed a new sign-in to your {_esc(_brand())} account."),
    ]
    detail = ""
    if when:
        detail += f"<strong>When:</strong> {_esc(when)}<br>"
    if device:
        detail += f"<strong>Device:</strong> {_esc(device)}"
    if detail:
        lines.append(_p(detail))
    lines.append(_p("If this was you, no action is needed."))
    if reset_url:
        lines.append(_p("If this <strong>wasn't</strong> you, secure your account now:"))
        lines.append(_button(reset_url, "Reset my password"))
    inner = "".join(lines)
    text_parts = [_greeting("en", name), "", f"New sign-in to your {_brand()} account."]
    if when:
        text_parts.append(f"When: {when}")
    if device:
        text_parts.append(f"Device: {device}")
    text_parts.append("If this wasn't you, reset your password immediately.")
    if reset_url:
        text_parts.append(reset_url)
    return subject, inner, "\n".join(text_parts), "New sign-in detected."


def _t_exam_link(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    exam_title = ctx.get("exam_title", "")
    exam_url = ctx["exam_url"]
    when = ctx.get("when")  # pre-formatted schedule string or None
    expires = ctx.get("expires")  # pre-formatted expiry string or None
    # Company name (the HR's tenant) — names the inviting organisation in the
    # subject + body. Empty for non-tenant sends (falls back to generic copy).
    org = (ctx.get("brand") or "").strip()
    orge = _esc(org)
    ttle = _esc(exam_title)
    loc = _loc(lang, {
        "en": {
            "subject": (
                (f"{org} · Your assessment: {exam_title}" if exam_title
                 else f"{org} · Your assessment invitation") if org
                else (f"Your exam: {exam_title}" if exam_title else "Your exam invitation")
            ),
            "pre": (
                f"{org} has invited you to take an assessment." if org
                else "You've been invited to take an exam."
            ),
            "lead": (
                ((f"<strong>{orge}</strong> has invited you to take the assessment "
                  f"<strong>{ttle}</strong>.") if exam_title
                 else f"<strong>{orge}</strong> has invited you to take an online assessment.")
                if org else
                ((f"You've been invited to take the assessment <strong>{ttle}</strong>.")
                 if exam_title else "You've been invited to take an online assessment.")
            ),
            "cta": "Start the exam",
            "fallback": "Or paste this link into your browser:",
            "sched": "Scheduled for:",
            "expiry": "Link valid until:",
            "outro": "All the best!",
        },
        "hi": {
            "subject": (
                (f"{org} · आपकी परीक्षा: {exam_title}" if exam_title
                 else f"{org} · आपका परीक्षा निमंत्रण") if org
                else (f"आपकी परीक्षा: {exam_title}" if exam_title else "आपका परीक्षा निमंत्रण")
            ),
            "pre": (
                f"{org} ने आपको एक मूल्यांकन देने के लिए आमंत्रित किया है।" if org
                else "आपको एक परीक्षा देने के लिए आमंत्रित किया गया है।"
            ),
            "lead": (
                ((f"<strong>{orge}</strong> ने आपको <strong>{ttle}</strong> मूल्यांकन देने के लिए आमंत्रित किया है।")
                 if exam_title
                 else f"<strong>{orge}</strong> ने आपको एक ऑनलाइन मूल्यांकन देने के लिए आमंत्रित किया है।")
                if org else
                ((f"आपको <strong>{ttle}</strong> मूल्यांकन देने के लिए आमंत्रित किया गया है।")
                 if exam_title else "आपको एक ऑनलाइन मूल्यांकन देने के लिए आमंत्रित किया गया है।")
            ),
            "cta": "परीक्षा शुरू करें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "sched": "निर्धारित समय:",
            "expiry": "लिंक मान्य है:",
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": (
                (f"{org} · మీ పరీక్ష: {exam_title}" if exam_title
                 else f"{org} · మీ పరీక్ష ఆహ్వానం") if org
                else (f"మీ పరీక్ష: {exam_title}" if exam_title else "మీ పరీక్ష ఆహ్వానం")
            ),
            "pre": (
                f"{org} మిమ్మల్ని ఒక మూల్యాంకనం రాయడానికి ఆహ్వానించింది." if org
                else "మీరు ఒక పరీక్ష రాయడానికి ఆహ్వానించబడ్డారు."
            ),
            "lead": (
                ((f"<strong>{orge}</strong> మిమ్మల్ని <strong>{ttle}</strong> మూల్యాంకనం రాయడానికి ఆహ్వానించింది.")
                 if exam_title
                 else f"<strong>{orge}</strong> మిమ్మల్ని ఒక ఆన్‌లైన్ మూల్యాంకనం రాయడానికి ఆహ్వానించింది.")
                if org else
                ((f"మీరు <strong>{ttle}</strong> మూల్యాంకనం రాయడానికి ఆహ్వానించబడ్డారు.")
                 if exam_title else "మీరు ఒక ఆన్‌లైన్ మూల్యాంకనం రాయడానికి ఆహ్వానించబడ్డారు.")
            ),
            "cta": "పరీక్షను ప్రారంభించండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "sched": "షెడ్యూల్:",
            "expiry": "లింక్ చెల్లుబాటు:",
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    meta = ""
    if when:
        meta += f"<strong>{loc['sched']}</strong> {_esc(when)}<br>"
    if expires:
        meta += f"<strong>{loc['expiry']}</strong> {_esc(expires)}"
    if meta:
        inner += _p(f'<span style="color:{_MUTED};font-size:14px;">{meta}</span>')
    inner += _button(exam_url, loc["cta"]) + _fallback_link(loc["fallback"], exam_url)
    inner += _p(loc["outro"])
    text = "\n".join(
        [_greeting(lang, name), "", html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
         (f"{loc['sched']} {when}" if when else ""), (f"{loc['expiry']} {expires}" if expires else ""),
         "", exam_url, "", loc["outro"]]
    )
    return loc["subject"], inner, text, loc["pre"]


def _t_interview_invite(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    interview_url = ctx.get("interview_url")  # None on reschedule
    when = ctx.get("when")
    rescheduled = ctx.get("rescheduled", False)
    # Company name (the HR's tenant) — names the inviting organisation.
    org = (ctx.get("brand") or "").strip()
    orge = _esc(org)
    jt = _esc(job_title)
    loc = _loc(lang, {
        "en": {
            "subject": (f"Your {org} interview for {job_title}" if org
                        else f"Your AI interview for {job_title}"),
            "pre": (f"{org} has invited you to an AI voice interview." if org
                    else "You've been invited to an AI voice interview."),
            "lead": (
                f"<strong>{orge}</strong> has invited you to an AI voice interview for "
                f"<strong>{jt}</strong>." if org
                else f"You've been invited to an AI voice interview for <strong>{jt}</strong>."
            ),
            "sched": "Your interview is scheduled for:",
            "anytime": "You can start the interview any time before the link expires.",
            "cta": "Join my interview",
            "fallback": "Or paste this link into your browser:",
            "reuse": "Please use the interview link from your original invitation email.",
            "outro": "Good luck!",
            "resched": "Your interview has been rescheduled.",
        },
        "hi": {
            "subject": (f"{job_title} के लिए {org} का एआई इंटरव्यू" if org
                        else f"{job_title} के लिए आपका एआई इंटरव्यू"),
            "pre": (f"{org} ने आपको एआई वॉयस इंटरव्यू के लिए आमंत्रित किया है।" if org
                    else "आपको एआई वॉयस इंटरव्यू के लिए आमंत्रित किया गया है।"),
            "lead": (
                f"<strong>{orge}</strong> ने आपको <strong>{jt}</strong> के लिए एआई वॉयस इंटरव्यू हेतु आमंत्रित किया है।"
                if org
                else f"आपको <strong>{jt}</strong> के लिए एआई वॉयस इंटरव्यू हेतु आमंत्रित किया गया है।"
            ),
            "sched": "आपका इंटरव्यू निर्धारित है:",
            "anytime": "आप लिंक समाप्त होने से पहले कभी भी इंटरव्यू शुरू कर सकते हैं।",
            "cta": "इंटरव्यू में शामिल हों",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "reuse": "कृपया अपने मूल निमंत्रण ईमेल के इंटरव्यू लिंक का उपयोग करें।",
            "outro": "शुभकामनाएँ!",
            "resched": "आपका इंटरव्यू पुनर्निर्धारित कर दिया गया है।",
        },
        "te": {
            "subject": (f"{job_title} కోసం {org} AI ఇంటర్వ్యూ" if org
                        else f"{job_title} కోసం మీ AI ఇంటర్వ్యూ"),
            "pre": (f"{org} మిమ్మల్ని AI వాయిస్ ఇంటర్వ్యూకి ఆహ్వానించింది." if org
                    else "మీరు AI వాయిస్ ఇంటర్వ్యూకి ఆహ్వానించబడ్డారు."),
            "lead": (
                f"<strong>{orge}</strong> మిమ్మల్ని <strong>{jt}</strong> కోసం AI వాయిస్ ఇంటర్వ్యూకి ఆహ్వానించింది."
                if org
                else f"మీరు <strong>{jt}</strong> కోసం AI వాయిస్ ఇంటర్వ్యూకి ఆహ్వానించబడ్డారు."
            ),
            "sched": "మీ ఇంటర్వ్యూ షెడ్యూల్ చేయబడింది:",
            "anytime": "లింక్ గడువు ముగియకముందు మీరు ఎప్పుడైనా ఇంటర్వ్యూను ప్రారంభించవచ్చు.",
            "cta": "నా ఇంటర్వ్యూలో చేరండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "reuse": "దయచేసి మీ అసలు ఆహ్వాన ఇమెయిల్‌లోని ఇంటర్వ్యూ లింక్‌ను ఉపయోగించండి.",
            "outro": "శుభాకాంక్షలు!",
            "resched": "మీ ఇంటర్వ్యూ తిరిగి షెడ్యూల్ చేయబడింది.",
        },
    })
    subject = ("[Rescheduled] " if rescheduled else "") + loc["subject"]
    inner = _p(_greeting(lang, name))
    if rescheduled:
        inner += _p(f"<strong>{loc['resched']}</strong>")
    inner += _p(loc["lead"])
    if when:
        inner += _p(f"<strong>{loc['sched']}</strong> {_esc(when)}")
    else:
        inner += _p(loc["anytime"])
    if interview_url:
        inner += _button(interview_url, loc["cta"]) + _fallback_link(loc["fallback"], interview_url)
    else:
        inner += _p(loc["reuse"])
    inner += _p(loc["outro"])
    text_parts = [_greeting(lang, name), ""]
    if rescheduled:
        text_parts.append(loc["resched"])
    text_parts.append(loc["lead"].replace("<strong>", "").replace("</strong>", ""))
    if when:
        text_parts.append(f"{loc['sched']} {when}")
    if interview_url:
        text_parts += ["", interview_url]
    else:
        text_parts.append(loc["reuse"])
    text_parts += ["", loc["outro"]]
    return subject, inner, "\n".join(text_parts), loc["pre"]


def _t_hr_credentials(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    # Internal account-provisioning email — EN (admin-facing).
    name = ctx.get("name")
    role_label = ctx.get("role_label", "manager")
    company = ctx.get("company")
    set_url = ctx.get("set_url")  # password-set link (preferred)
    login_url = ctx.get("login_url", settings.app_base_url)
    subject = f"Your {_brand()} {role_label} account is ready"
    where = f" for <strong>{_esc(company)}</strong>" if company else ""
    inner = (
        _p(_greeting("en", name))
        + _p(f"An account has been created for you as <strong>{_esc(role_label)}</strong>{where} on {_esc(_brand())}.")
    )
    if set_url:
        inner += _p("To get started, set your password using the secure link below:")
        inner += _button(set_url, "Set my password")
        inner += _fallback_link("Or paste this link into your browser:", set_url)
        inner += _p(f'<span style="color:{_MUTED};font-size:13px;">This link expires soon and can be used once.</span>')
    else:
        inner += _p(f'Sign in here: <a href="{_esc_attr(login_url)}" style="color:{_BRAND_COLOR};">{_esc(login_url)}</a>')
        inner += _p("You will be asked to set a new password on your first sign-in.")
    text_parts = [
        _greeting("en", name), "",
        f"An account has been created for you as {role_label}"
        + (f" for {company}" if company else "") + f" on {_brand()}.",
    ]
    if set_url:
        text_parts += ["Set your password:", set_url]
    else:
        text_parts += [f"Sign in: {login_url}", "You'll set a new password on first sign-in."]
    return subject, inner, "\n".join(text_parts), "Your account is ready."


def _t_decision(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """Application-decision email to a candidate: shortlisted | hired | rejected.

    ctx: name, job_title, decision. Tone is warm for shortlist/hire and respectful
    for a rejection. No CTA — applicants aren't portal users; next steps (exam /
    interview links) arrive as their own emails.
    """
    name = ctx.get("name")
    job = ctx.get("job_title") or "the role"
    decision = ctx.get("decision", "rejected")
    jobe = _esc(job)
    copy: dict[str, dict[str, dict[str, str]]] = {
        "shortlisted": {
            "en": {
                "subject": f"You've been shortlisted for {job}",
                "lead": f"Good news — you've been <strong>shortlisted</strong> for {jobe}.",
                "body": "Our team will be in touch with the next steps (such as an assessment or interview) shortly.",
                "outro": "Congratulations, and well done!",
            },
            "hi": {
                "subject": f"{job} के लिए आपको शॉर्टलिस्ट किया गया है",
                "lead": f"खुशखबरी — {jobe} के लिए आपको <strong>शॉर्टलिस्ट</strong> किया गया है।",
                "body": "हमारी टीम जल्द ही अगले चरणों (जैसे मूल्यांकन या इंटरव्यू) के लिए आपसे संपर्क करेगी।",
                "outro": "बधाई हो!",
            },
            "te": {
                "subject": f"{job} కోసం మీరు షార్ట్‌లిస్ట్ అయ్యారు",
                "lead": f"శుభవార్త — {jobe} కోసం మీరు <strong>షార్ట్‌లిస్ట్</strong> అయ్యారు.",
                "body": "తదుపరి దశల (మూల్యాంకనం లేదా ఇంటర్వ్యూ వంటివి) కోసం మా బృందం త్వరలో మిమ్మల్ని సంప్రదిస్తుంది.",
                "outro": "అభినందనలు!",
            },
        },
        "hired": {
            "en": {
                "subject": f"Congratulations — you've been selected for {job}",
                "lead": f"We're delighted to let you know you've been <strong>selected</strong> for {jobe}.",
                "body": "Our team will reach out shortly with the offer details and next steps.",
                "outro": "Welcome aboard!",
            },
            "hi": {
                "subject": f"बधाई हो — {job} के लिए आपका चयन हुआ है",
                "lead": f"हमें यह बताते हुए खुशी है कि {jobe} के लिए आपका <strong>चयन</strong> हुआ है।",
                "body": "हमारी टीम जल्द ही ऑफर विवरण और अगले चरणों के साथ आपसे संपर्क करेगी।",
                "outro": "आपका स्वागत है!",
            },
            "te": {
                "subject": f"అభినందనలు — {job} కోసం మీరు ఎంపికయ్యారు",
                "lead": f"{jobe} కోసం మీరు <strong>ఎంపికయ్యారు</strong> అని తెలియజేయడానికి సంతోషిస్తున్నాము.",
                "body": "ఆఫర్ వివరాలు, తదుపరి దశలతో మా బృందం త్వరలో మిమ్మల్ని సంప్రదిస్తుంది.",
                "outro": "స్వాగతం!",
            },
        },
        "rejected": {
            "en": {
                "subject": f"Update on your application for {job}",
                "lead": f"Thank you for your interest in {jobe} and for the time you invested.",
                "body": "After careful consideration, we won't be moving forward with your application at this time. This was a difficult decision and reflects our current needs, not your ability.",
                "outro": "We genuinely wish you the very best in your search.",
            },
            "hi": {
                "subject": f"{job} के लिए आपके आवेदन पर अपडेट",
                "lead": f"{jobe} में आपकी रुचि और आपके समय के लिए धन्यवाद।",
                "body": "सावधानीपूर्वक विचार करने के बाद, हम इस समय आपके आवेदन को आगे नहीं बढ़ा पाएंगे। यह एक कठिन निर्णय था और हमारी वर्तमान आवश्यकताओं को दर्शाता है, न कि आपकी योग्यता को।",
                "outro": "हम आपकी आगे की खोज के लिए शुभकामनाएँ देते हैं।",
            },
            "te": {
                "subject": f"{job} కోసం మీ దరఖాస్తుపై అప్‌డేట్",
                "lead": f"{jobe}పై మీ ఆసక్తికి, మీరు వెచ్చించిన సమయానికి ధన్యవాదాలు.",
                "body": "జాగ్రత్తగా పరిశీలించిన తర్వాత, ప్రస్తుతం మీ దరఖాస్తును ముందుకు తీసుకెళ్లలేకపోతున్నాము. ఇది కష్టమైన నిర్ణయం, ఇది మా ప్రస్తుత అవసరాలను సూచిస్తుంది, మీ సామర్థ్యాన్ని కాదు.",
                "outro": "మీ తదుపరి అన్వేషణలో మీకు అన్ని శుభాకాంక్షలు.",
            },
        },
    }
    row = copy.get(decision, copy["rejected"])
    loc = row.get(lang, row["en"])
    # Prefix the subject with the inviting company so the inbox line is branded
    # too; the wordmark + sender name already carry the company via the layout.
    org = (ctx.get("brand") or "").strip()
    subject = f"{org} · {loc['subject']}" if org else loc["subject"]
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(loc["body"]) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        loc["lead"].replace("<strong>", "").replace("</strong>", ""),
        loc["body"], "", loc["outro"],
    ])
    return subject, inner, text, subject


def _t_generic(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """Catch-all for ad-hoc platform notifications (approvals, updates, alerts).

    ctx: title, body (plain text — escaped), optional cta_label + cta_url, name.
    """
    name = ctx.get("name")
    title = ctx.get("title", _brand())
    body = ctx.get("body", "")
    cta_label = ctx.get("cta_label")
    cta_url = ctx.get("cta_url")
    inner = ""
    if name:
        inner += _p(_greeting(lang, name))
    inner += _p(f"<strong>{_esc(title)}</strong>")
    if body:
        # Preserve simple line breaks from the producer's plain text.
        inner += _p(_esc(body).replace("\n", "<br>"))
    text_parts = ([_greeting(lang, name), ""] if name else []) + [title, "", body]
    if cta_label and cta_url:
        inner += _button(cta_url, cta_label)
        inner += _fallback_link("Or paste this link into your browser:", cta_url)
        text_parts += ["", cta_url]
    return title, inner, "\n".join(text_parts), title


_BUILDERS = {
    "welcome": _t_welcome,
    "email_verify": _t_email_verify,
    "password_reset": _t_password_reset,
    "login_alert": _t_login_alert,
    "exam_link": _t_exam_link,
    "interview_invite": _t_interview_invite,
    "hr_credentials": _t_hr_credentials,
    "decision": _t_decision,
    "generic": _t_generic,
}


def render(template: str, lang: str, ctx: dict) -> RenderedEmail:
    """Render ``template`` in ``lang`` with ``ctx`` → branded (subject, html, text).

    Unknown templates fall back to the generic builder; unknown languages to EN.
    """
    builder = _BUILDERS.get(template, _t_generic)
    norm = _norm_lang(lang)
    subject, inner, text, preheader = builder(norm, ctx)
    # ctx["brand"] (a tenant company name) overrides the wordmark + footer.
    html = _layout(inner, preheader=preheader, brand=ctx.get("brand"))
    return RenderedEmail(subject=subject, html=html, text=text)
