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
    return settings.email_from_name or "AntHire"


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
    for a rejection. No CTA: next steps (exam / interview links) arrive as their
    own emails, and a decision is not something the candidate acts on here.

    This used to read "applicants aren't portal users", which stopped being true
    when activation gave them an account and an applications page. The absence
    of a CTA is now a choice about this particular email rather than a fact
    about applicants — a rejection with a button on it would be worse, not more
    helpful.
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


def _t_exam_reminder(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """Deadline nudge for an assessment link that has not been opened.

    Carries no link: assessment tokens are HMAC-hashed and unrecoverable, so the
    candidate is pointed back at the invitation they already hold. See the note
    in ``reminders.py`` for why minting a fresh token here would be worse.
    """
    name = ctx.get("name")
    title = ctx.get("exam_title", "")
    expires = ctx.get("expires")
    soon = ctx.get("window") == "1h"
    ttle = _esc(title)
    loc = _loc(lang, {
        "en": {
            "subject": (
                (f"Last chance today: {title}" if title else "Your assessment closes today")
                if soon else
                (f"Reminder: {title} closes tomorrow" if title
                 else "Reminder: your assessment closes tomorrow")
            ),
            "pre": "Your assessment link is about to expire.",
            "lead": (
                f"A quick reminder that your assessment <strong>{ttle}</strong> is still open."
                if title else "A quick reminder that your assessment is still open."
            ),
            "urgency": "It closes within the hour." if soon else "It closes tomorrow.",
            "expiry": "Closes:",
            "how": (
                "Use the link in the invitation email we sent you earlier to begin. "
                "If you can no longer find it, reply to this message and we will send a new one."
            ),
            "outro": "All the best!",
        },
        "hi": {
            "subject": (
                (f"आज आखिरी मौका: {title}" if title else "आपकी परीक्षा आज बंद हो रही है")
                if soon else
                (f"अनुस्मारक: {title} कल बंद हो रही है" if title
                 else "अनुस्मारक: आपकी परीक्षा कल बंद हो रही है")
            ),
            "pre": "आपका परीक्षा लिंक जल्द ही समाप्त हो रहा है।",
            "lead": (
                f"यह याद दिलाने के लिए कि आपकी परीक्षा <strong>{ttle}</strong> अभी भी खुली है।"
                if title else "यह याद दिलाने के लिए कि आपकी परीक्षा अभी भी खुली है।"
            ),
            "urgency": "यह एक घंटे के भीतर बंद हो जाएगी।" if soon else "यह कल बंद हो जाएगी।",
            "expiry": "बंद होने का समय:",
            "how": (
                "शुरू करने के लिए पहले भेजे गए निमंत्रण ईमेल का लिंक उपयोग करें। "
                "यदि वह नहीं मिल रहा है, तो इस संदेश का उत्तर दें और हम नया लिंक भेज देंगे।"
            ),
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": (
                (f"ఈరోజే చివరి అవకాశం: {title}" if title else "మీ పరీక్ష ఈరోజు ముగుస్తుంది")
                if soon else
                (f"గుర్తుచేయడం: {title} రేపు ముగుస్తుంది" if title
                 else "గుర్తుచేయడం: మీ పరీక్ష రేపు ముగుస్తుంది")
            ),
            "pre": "మీ పరీక్ష లింక్ త్వరలో ముగియనుంది.",
            "lead": (
                f"మీ పరీక్ష <strong>{ttle}</strong> ఇంకా అందుబాటులో ఉందని గుర్తుచేస్తున్నాము."
                if title else "మీ పరీక్ష ఇంకా అందుబాటులో ఉందని గుర్తుచేస్తున్నాము."
            ),
            "urgency": "ఇది ఒక గంటలోపు ముగుస్తుంది." if soon else "ఇది రేపు ముగుస్తుంది.",
            "expiry": "ముగింపు:",
            "how": (
                "ప్రారంభించడానికి మేము గతంలో పంపిన ఆహ్వాన ఇమెయిల్‌లోని లింక్‌ను ఉపయోగించండి. "
                "అది దొరకకపోతే, ఈ సందేశానికి ప్రత్యుత్తరం ఇవ్వండి, మేము కొత్తది పంపుతాము."
            ),
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"] + " " + _esc(loc["urgency"]))
    if expires:
        inner += _p(
            f'<span style="color:{_MUTED};font-size:14px;">'
            f'<strong>{loc["expiry"]}</strong> {_esc(expires)}</span>'
        )
    inner += _p(_esc(loc["how"])) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
        loc["urgency"],
        (f"{loc['expiry']} {expires}" if expires else ""), "",
        loc["how"], "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_interview_reminder(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """Nudge before a scheduled interview, or before its link lapses."""
    name = ctx.get("name")
    job = ctx.get("job_title", "")
    when = ctx.get("when")
    expires = ctx.get("expires")
    soon = ctx.get("window") == "1h"
    jobe = _esc(job)
    loc = _loc(lang, {
        "en": {
            "subject": (
                (f"Your interview for {job} is within the hour" if job
                 else "Your interview is within the hour")
                if soon else
                (f"Reminder: your interview for {job} is tomorrow" if job
                 else "Reminder: your interview is tomorrow")
            ),
            "pre": "Your AI interview is coming up.",
            "lead": (
                f"This is a reminder about your interview for <strong>{jobe}</strong>."
                if job else "This is a reminder about your upcoming interview."
            ),
            "when": "Scheduled for:",
            "expiry": "Link valid until:",
            "prep": (
                "Find a quiet room with a stable internet connection and allow about "
                "fifteen minutes. You will be asked to grant camera and microphone "
                "access when the interview begins."
            ),
            "how": "Use the link in your invitation email to join.",
            "outro": "Good luck!",
        },
        "hi": {
            "subject": (
                (f"{job} के लिए आपका साक्षात्कार एक घंटे में है" if job
                 else "आपका साक्षात्कार एक घंटे में है")
                if soon else
                (f"अनुस्मारक: {job} के लिए आपका साक्षात्कार कल है" if job
                 else "अनुस्मारक: आपका साक्षात्कार कल है")
            ),
            "pre": "आपका AI साक्षात्कार आने वाला है।",
            "lead": (
                f"यह <strong>{jobe}</strong> के लिए आपके साक्षात्कार का अनुस्मारक है।"
                if job else "यह आपके आगामी साक्षात्कार का अनुस्मारक है।"
            ),
            "when": "निर्धारित समय:",
            "expiry": "लिंक मान्य है:",
            "prep": (
                "स्थिर इंटरनेट कनेक्शन के साथ एक शांत कमरा चुनें और लगभग पंद्रह मिनट का "
                "समय रखें। साक्षात्कार शुरू होने पर आपको कैमरा और माइक्रोफ़ोन की अनुमति देनी होगी।"
            ),
            "how": "शामिल होने के लिए अपने निमंत्रण ईमेल का लिंक उपयोग करें।",
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": (
                (f"{job} కోసం మీ ఇంటర్వ్యూ ఒక గంటలో ఉంది" if job
                 else "మీ ఇంటర్వ్యూ ఒక గంటలో ఉంది")
                if soon else
                (f"గుర్తుచేయడం: {job} కోసం మీ ఇంటర్వ్యూ రేపు ఉంది" if job
                 else "గుర్తుచేయడం: మీ ఇంటర్వ్యూ రేపు ఉంది")
            ),
            "pre": "మీ AI ఇంటర్వ్యూ రాబోతోంది.",
            "lead": (
                f"ఇది <strong>{jobe}</strong> కోసం మీ ఇంటర్వ్యూ గుర్తుచేయడం."
                if job else "ఇది మీ రాబోయే ఇంటర్వ్యూ గుర్తుచేయడం."
            ),
            "when": "షెడ్యూల్:",
            "expiry": "లింక్ చెల్లుబాటు:",
            "prep": (
                "స్థిరమైన ఇంటర్నెట్ కనెక్షన్‌తో నిశ్శబ్ద గదిని ఎంచుకోండి, సుమారు పదిహేను "
                "నిమిషాలు కేటాయించండి. ఇంటర్వ్యూ ప్రారంభమైనప్పుడు కెమెరా, మైక్రోఫోన్ అనుమతి ఇవ్వాలి."
            ),
            "how": "చేరడానికి మీ ఆహ్వాన ఇమెయిల్‌లోని లింక్‌ను ఉపయోగించండి.",
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    meta = ""
    if when:
        meta += f'<strong>{loc["when"]}</strong> {_esc(when)}<br>'
    if expires:
        meta += f'<strong>{loc["expiry"]}</strong> {_esc(expires)}'
    if meta:
        inner += _p(f'<span style="color:{_MUTED};font-size:14px;">{meta}</span>')
    inner += _p(_esc(loc["prep"])) + _p(_esc(loc["how"])) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
        (f"{loc['when']} {when}" if when else ""),
        (f"{loc['expiry']} {expires}" if expires else ""), "",
        loc["prep"], "", loc["how"], "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_link_expired(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """A link lapsed unused — the expiry notice.

    A missed interview SLOT has its own email (``interview_no_show``): that link
    dies ten minutes after the slot, long before it formally expires. This one
    covers exam links and unscheduled interview links reaching their deadline.
    The copy is deliberately neutral about consequence. Missing a window is NOT
    a rejection — under D-05 only a person ends a candidacy — so this must not
    imply one, and must not promise reinstatement either.
    """
    name = ctx.get("name")
    what = ctx.get("what", "")
    kind = ctx.get("kind", "exam")
    expired = ctx.get("expired")
    whate = _esc(what)
    loc = _loc(lang, {
        "en": {
            "subject": (
                "Your interview window has closed" if kind == "interview"
                else "Your task window has closed" if kind == "task"
                else "Your assessment window has closed"
            ),
            "pre": (
                "The window for your interview has closed." if kind == "interview"
                else "The window for your task has closed." if kind == "task"
                else "The window for your assessment has closed."
            ),
            "lead": (
                f"The window for <strong>{whate}</strong> closed without a submission."
                if what and kind == "task" else
                f"The window for <strong>{whate}</strong> closed without it being started."
                if what else "Your scheduled window closed without being started."
            ),
            "closed": "Closed:",
            "next": (
                "No action is needed from you right now. The hiring team has been notified "
                "and will be in touch if they would like to arrange another slot."
            ),
            "outro": "Thank you for your interest.",
        },
        "hi": {
            "subject": (
                "आपके साक्षात्कार की अवधि समाप्त हो गई" if kind == "interview"
                else "आपके टास्क की अवधि समाप्त हो गई" if kind == "task"
                else "आपकी परीक्षा की अवधि समाप्त हो गई"
            ),
            "pre": (
                "आपके साक्षात्कार की अवधि समाप्त हो गई है।" if kind == "interview"
                else "आपके टास्क की अवधि समाप्त हो गई है।" if kind == "task"
                else "आपकी परीक्षा की अवधि समाप्त हो गई है।"
            ),
            "lead": (
                f"<strong>{whate}</strong> की अवधि बिना सबमिट किए समाप्त हो गई।"
                if what and kind == "task" else
                f"<strong>{whate}</strong> की अवधि बिना शुरू हुए समाप्त हो गई।"
                if what else "आपकी निर्धारित अवधि बिना शुरू हुए समाप्त हो गई।"
            ),
            "closed": "समाप्त:",
            "next": (
                "अभी आपकी ओर से किसी कार्रवाई की आवश्यकता नहीं है। भर्ती टीम को सूचित कर "
                "दिया गया है और यदि वे कोई और समय देना चाहेंगे तो वे संपर्क करेंगे।"
            ),
            "outro": "आपकी रुचि के लिए धन्यवाद।",
        },
        "te": {
            "subject": (
                "మీ ఇంటర్వ్యూ వ్యవధి ముగిసింది" if kind == "interview"
                else "మీ టాస్క్ వ్యవధి ముగిసింది" if kind == "task"
                else "మీ పరీక్ష వ్యవధి ముగిసింది"
            ),
            "pre": (
                "మీ ఇంటర్వ్యూ వ్యవధి ముగిసింది." if kind == "interview"
                else "మీ టాస్క్ వ్యవధి ముగిసింది." if kind == "task"
                else "మీ పరీక్ష వ్యవధి ముగిసింది."
            ),
            "lead": (
                f"<strong>{whate}</strong> వ్యవధి సమర్పించకుండానే ముగిసింది."
                if what and kind == "task" else
                f"<strong>{whate}</strong> వ్యవధి ప్రారంభించకుండానే ముగిసింది."
                if what else "మీ నిర్ణీత వ్యవధి ప్రారంభించకుండానే ముగిసింది."
            ),
            "closed": "ముగిసినది:",
            "next": (
                "ప్రస్తుతం మీ నుండి ఎటువంటి చర్య అవసరం లేదు. నియామక బృందానికి తెలియజేయబడింది, "
                "వారు మరో సమయం ఇవ్వాలనుకుంటే మిమ్మల్ని సంప్రదిస్తారు."
            ),
            "outro": "మీ ఆసక్తికి ధన్యవాదాలు.",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    if expired:
        inner += _p(
            f'<span style="color:{_MUTED};font-size:14px;">'
            f'<strong>{loc["closed"]}</strong> {_esc(expired)}</span>'
        )
    inner += _p(_esc(loc["next"])) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
        (f"{loc['closed']} {expired}" if expired else ""), "",
        loc["next"], "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_link_expiring(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """The expiry warning: the link itself stops working within the hour.

    Distinct from the reminders. A reminder is about an appointment or a
    deadline coming up; this is the last notice before the link goes dead —
    the difference between "your interview is tomorrow" and "this link will
    not open after 6 PM". Carries no link, for the same reason the reminders
    carry none (tokens are HMAC-hashed and cannot be re-sent).
    """
    name = ctx.get("name")
    what = ctx.get("what", "")
    kind = ctx.get("kind", "exam")
    expires = ctx.get("expires")
    whate = _esc(what)
    interview = kind == "interview"
    loc = _loc(lang, {
        "en": {
            "subject": (
                f"Last chance: your link for {what} expires soon" if what
                else ("Last chance: your interview link expires soon" if interview
                      else "Last chance: your assessment link expires soon")
            ),
            "pre": "Your link stops working soon.",
            "lead": (
                f"Your link for <strong>{whate}</strong> will stop working within the hour."
                if what else
                ("Your interview link will stop working within the hour." if interview
                 else "Your assessment link will stop working within the hour.")
            ),
            "expiry": "Expires:",
            "how": (
                "If you would still like to take part, use the link in your invitation "
                "email before then. After it expires, it will no longer open."
            ),
            "outro": "All the best!",
        },
        "hi": {
            "subject": (
                f"आखिरी मौका: {what} का लिंक जल्द समाप्त हो रहा है" if what
                else ("आखिरी मौका: आपका साक्षात्कार लिंक जल्द समाप्त हो रहा है" if interview
                      else "आखिरी मौका: आपका परीक्षा लिंक जल्द समाप्त हो रहा है")
            ),
            "pre": "आपका लिंक जल्द ही काम करना बंद कर देगा।",
            "lead": (
                f"<strong>{whate}</strong> के लिए आपका लिंक एक घंटे के भीतर काम करना बंद कर देगा।"
                if what else
                ("आपका साक्षात्कार लिंक एक घंटे के भीतर काम करना बंद कर देगा।" if interview
                 else "आपका परीक्षा लिंक एक घंटे के भीतर काम करना बंद कर देगा।")
            ),
            "expiry": "समाप्ति:",
            "how": (
                "यदि आप अभी भी भाग लेना चाहते हैं, तो उससे पहले अपने निमंत्रण ईमेल के लिंक "
                "का उपयोग करें। समाप्त होने के बाद यह लिंक नहीं खुलेगा।"
            ),
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": (
                f"చివరి అవకాశం: {what} లింక్ త్వరలో ముగుస్తుంది" if what
                else ("చివరి అవకాశం: మీ ఇంటర్వ్యూ లింక్ త్వరలో ముగుస్తుంది" if interview
                      else "చివరి అవకాశం: మీ పరీక్ష లింక్ త్వరలో ముగుస్తుంది")
            ),
            "pre": "మీ లింక్ త్వరలో పనిచేయడం ఆగిపోతుంది.",
            "lead": (
                f"<strong>{whate}</strong> కోసం మీ లింక్ ఒక గంటలోపు పనిచేయడం ఆగిపోతుంది."
                if what else
                ("మీ ఇంటర్వ్యూ లింక్ ఒక గంటలోపు పనిచేయడం ఆగిపోతుంది." if interview
                 else "మీ పరీక్ష లింక్ ఒక గంటలోపు పనిచేయడం ఆగిపోతుంది.")
            ),
            "expiry": "ముగింపు:",
            "how": (
                "మీరు ఇంకా పాల్గొనాలనుకుంటే, ఆ లోపు మీ ఆహ్వాన ఇమెయిల్‌లోని లింక్‌ను "
                "ఉపయోగించండి. గడువు ముగిసిన తర్వాత ఈ లింక్ తెరవబడదు."
            ),
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    if expires:
        inner += _p(
            f'<span style="color:{_MUTED};font-size:14px;">'
            f'<strong>{loc["expiry"]}</strong> {_esc(expires)}</span>'
        )
    inner += _p(_esc(loc["how"])) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
        (f"{loc['expiry']} {expires}" if expires else ""), "",
        loc["how"], "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_interview_no_show(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """The follow-up when a scheduled interview slot passed unstarted.

    The link stops starting the interview ten minutes after the slot (the join
    window), so this is sent then rather than days later when the link formally
    expires. Neutral by requirement: a missed slot is not a rejection (D-05),
    and nothing here may imply one — nor promise a new slot, which is HR's to
    offer. "Missed" is avoided in the subject for the same reason; the facts
    are stated without assigning fault.
    """
    name = ctx.get("name")
    job = ctx.get("job_title", "")
    when = ctx.get("when")
    jobe = _esc(job)
    loc = _loc(lang, {
        "en": {
            "subject": (
                f"Your interview time for {job} has passed" if job
                else "Your interview time has passed"
            ),
            "pre": "Your scheduled interview time has passed.",
            "lead": (
                f"Your interview for <strong>{jobe}</strong> was scheduled for a time that "
                "has now passed, and it was not started."
                if job else
                "Your scheduled interview time has passed, and the interview was not started."
            ),
            "when": "Scheduled for:",
            "next": (
                "The link in your invitation can no longer be used to start it. We have let "
                "the hiring team know, and they can arrange a new time with you if they "
                "would like to. No action is needed from you right now."
            ),
            "help": (
                "If something went wrong on your side, such as a connection or device "
                "problem, you can reply to this email to let them know."
            ),
            "outro": "Thank you for your interest.",
        },
        "hi": {
            "subject": (
                f"{job} के लिए आपके साक्षात्कार का समय बीत गया है" if job
                else "आपके साक्षात्कार का समय बीत गया है"
            ),
            "pre": "आपके साक्षात्कार का निर्धारित समय बीत गया है।",
            "lead": (
                f"<strong>{jobe}</strong> के लिए आपका साक्षात्कार जिस समय निर्धारित था, वह "
                "बीत चुका है और साक्षात्कार शुरू नहीं हुआ।"
                if job else
                "आपके साक्षात्कार का निर्धारित समय बीत चुका है और साक्षात्कार शुरू नहीं हुआ।"
            ),
            "when": "निर्धारित समय:",
            "next": (
                "आपके निमंत्रण के लिंक से अब इसे शुरू नहीं किया जा सकता। हमने भर्ती टीम को "
                "सूचित कर दिया है; यदि वे चाहें तो आपके साथ नया समय तय कर सकते हैं। अभी "
                "आपकी ओर से किसी कार्रवाई की आवश्यकता नहीं है।"
            ),
            "help": (
                "यदि आपकी ओर से कोई समस्या आई थी, जैसे इंटरनेट या डिवाइस की दिक्कत, तो आप "
                "इस ईमेल का उत्तर देकर उन्हें बता सकते हैं।"
            ),
            "outro": "आपकी रुचि के लिए धन्यवाद।",
        },
        "te": {
            "subject": (
                f"{job} కోసం మీ ఇంటర్వ్యూ సమయం దాటిపోయింది" if job
                else "మీ ఇంటర్వ్యూ సమయం దాటిపోయింది"
            ),
            "pre": "మీ ఇంటర్వ్యూ నిర్ణీత సమయం దాటిపోయింది.",
            "lead": (
                f"<strong>{jobe}</strong> కోసం మీ ఇంటర్వ్యూకు నిర్ణయించిన సమయం దాటిపోయింది, "
                "ఇంటర్వ్యూ ప్రారంభం కాలేదు."
                if job else
                "మీ ఇంటర్వ్యూ నిర్ణీత సమయం దాటిపోయింది, ఇంటర్వ్యూ ప్రారంభం కాలేదు."
            ),
            "when": "షెడ్యూల్:",
            "next": (
                "మీ ఆహ్వానంలోని లింక్‌తో ఇకపై దీన్ని ప్రారంభించలేరు. నియామక బృందానికి "
                "తెలియజేశాము; వారు కావాలనుకుంటే మీతో కొత్త సమయం ఏర్పాటు చేయవచ్చు. "
                "ప్రస్తుతం మీ నుండి ఎటువంటి చర్య అవసరం లేదు."
            ),
            "help": (
                "మీ వైపు ఇంటర్నెట్ లేదా పరికరం సమస్య వంటిది ఏదైనా జరిగి ఉంటే, ఈ ఇమెయిల్‌కు "
                "ప్రత్యుత్తరం ఇచ్చి వారికి తెలియజేయవచ్చు."
            ),
            "outro": "మీ ఆసక్తికి ధన్యవాదాలు.",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    if when:
        inner += _p(
            f'<span style="color:{_MUTED};font-size:14px;">'
            f'<strong>{loc["when"]}</strong> {_esc(when)}</span>'
        )
    inner += _p(_esc(loc["next"])) + _p(_esc(loc["help"])) + _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
        (f"{loc['when']} {when}" if when else ""), "",
        loc["next"], "", loc["help"], "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_results_ready(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """The scorecard is available.

    Carries no score. A composite number stripped of its rubric, its evidence
    and its improvement notes is the worst possible framing of an assessment
    result, and an inbox is not where someone should first read one.
    """
    name = ctx.get("name")
    job = ctx.get("job_title", "")
    url = ctx.get("cta_url")
    jobe = _esc(job)
    loc = _loc(lang, {
        "en": {
            "subject": (
                f"Your interview results for {job} are ready" if job
                else "Your interview results are ready"
            ),
            "pre": "Your scorecard is now available.",
            "lead": (
                f"Your scorecard for <strong>{jobe}</strong> is ready to view."
                if job else "Your interview scorecard is ready to view."
            ),
            "detail": (
                "It covers how you came across on communication, technical knowledge, "
                "problem solving and confidence, along with specific suggestions for "
                "what to work on next."
            ),
            "cta": "View your scorecard",
            "fallback": "Or paste this link into your browser:",
            "nolink": "Sign in to the platform to view it.",
            "outro": "Thank you for taking the time.",
        },
        "hi": {
            "subject": (
                f"{job} के लिए आपके साक्षात्कार परिणाम तैयार हैं" if job
                else "आपके साक्षात्कार परिणाम तैयार हैं"
            ),
            "pre": "आपका स्कोरकार्ड अब उपलब्ध है।",
            "lead": (
                f"<strong>{jobe}</strong> के लिए आपका स्कोरकार्ड देखने के लिए तैयार है।"
                if job else "आपका साक्षात्कार स्कोरकार्ड देखने के लिए तैयार है।"
            ),
            "detail": (
                "इसमें संचार, तकनीकी ज्ञान, समस्या-समाधान और आत्मविश्वास पर आपका प्रदर्शन "
                "शामिल है, साथ ही आगे किन बातों पर काम करना है इसके सुझाव भी।"
            ),
            "cta": "अपना स्कोरकार्ड देखें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "nolink": "इसे देखने के लिए प्लेटफ़ॉर्म पर साइन इन करें।",
            "outro": "समय देने के लिए धन्यवाद।",
        },
        "te": {
            "subject": (
                f"{job} కోసం మీ ఇంటర్వ్యూ ఫలితాలు సిద్ధంగా ఉన్నాయి" if job
                else "మీ ఇంటర్వ్యూ ఫలితాలు సిద్ధంగా ఉన్నాయి"
            ),
            "pre": "మీ స్కోర్‌కార్డ్ ఇప్పుడు అందుబాటులో ఉంది.",
            "lead": (
                f"<strong>{jobe}</strong> కోసం మీ స్కోర్‌కార్డ్ చూడటానికి సిద్ధంగా ఉంది."
                if job else "మీ ఇంటర్వ్యూ స్కోర్‌కార్డ్ చూడటానికి సిద్ధంగా ఉంది."
            ),
            "detail": (
                "ఇందులో కమ్యూనికేషన్, సాంకేతిక పరిజ్ఞానం, సమస్య పరిష్కారం మరియు ఆత్మవిశ్వాసంపై "
                "మీ ప్రదర్శన, అలాగే తదుపరి దేనిపై దృష్టి పెట్టాలో సూచనలు ఉన్నాయి."
            ),
            "cta": "మీ స్కోర్‌కార్డ్ చూడండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "nolink": "దీన్ని చూడటానికి ప్లాట్‌ఫారమ్‌లో సైన్ ఇన్ చేయండి.",
            "outro": "సమయం కేటాయించినందుకు ధన్యవాదాలు.",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(_esc(loc["detail"]))
    if url:
        inner += _button(url, loc["cta"]) + _fallback_link(loc["fallback"], url)
    else:
        inner += _p(_esc(loc["nolink"]))
    inner += _p(loc["outro"])
    text = "\n".join([
        _greeting(lang, name), "",
        html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")), "",
        loc["detail"], "", (url or loc["nolink"]), "", loc["outro"],
    ])
    return loc["subject"], inner, text, loc["pre"]


def _t_application_received(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """Application confirmation, with a link to activate the account.

    ctx: name, job_title, company, set_url (optional), applications_url.

    Candidate-facing, so EN/HI/TE like the other three — the staff-only
    templates are English by design, this is not one of them.

    Two shapes, one email. With ``set_url`` the applicant has no account yet
    and the CTA sets a password; without it they already have one and the CTA
    goes to their applications. The alternative was two templates that would
    drift apart, when the only real difference is which door the button opens.
    """
    name = ctx.get("name")
    job = ctx.get("job_title") or "the role"
    company = ctx.get("company")
    set_url = ctx.get("set_url")
    apps_url = ctx.get("applications_url") or settings.app_base_url
    jobe = _esc(job)
    orge = f"<strong>{_esc(company)}</strong>" if company else ""

    # The lead names the company in each language's own word order. It used to
    # splice the English " at <strong>Company</strong>" into every language, and
    # the same HTML went into the plain-text part.
    def lead(lang_key: str, job_part: str, company_part: str) -> str:
        if lang_key == "hi":
            where = f"{company_part} में " if company_part else ""
            return f"धन्यवाद — {where}{job_part} के लिए आपका आवेदन मिल गया है।"
        if lang_key == "te":
            where = f"{company_part}లో " if company_part else ""
            return f"ధన్యవాదాలు — {where}{job_part} కోసం మీ దరఖాస్తు అందింది."
        at = f" at {company_part}" if company_part else ""
        return f"Thanks — your application for {job_part}{at} is in."

    copy = {
        "en": {
            "subject": f"We have your application for {job}",
            "activate": (
                "Set a password to track it. You will be able to see which stage "
                "you are at and what happens next, in one place."
            ),
            "cta_set": "Set my password",
            "signed_in": "You can follow its progress from your applications page.",
            "cta_view": "View my applications",
            "outro": "We will email you when there is news.",
            "expiry": "This link can be used once and expires in 7 days.",
        },
        "hi": {
            "subject": f"{job} के लिए आपका आवेदन मिल गया",
            "activate": (
                "इसे ट्रैक करने के लिए पासवर्ड सेट करें। आप एक ही जगह देख सकेंगे कि "
                "आप किस चरण में हैं और आगे क्या होगा।"
            ),
            "cta_set": "पासवर्ड सेट करें",
            "signed_in": "आप अपने आवेदन पृष्ठ से इसकी प्रगति देख सकते हैं।",
            "cta_view": "मेरे आवेदन देखें",
            "outro": "कोई अपडेट होने पर हम आपको ईमेल करेंगे।",
            "expiry": "यह लिंक एक बार उपयोग हो सकता है और 7 दिनों में समाप्त हो जाएगा।",
        },
        "te": {
            "subject": f"{job} కోసం మీ దరఖాస్తు అందింది",
            "activate": (
                "దీన్ని ట్రాక్ చేయడానికి పాస్‌వర్డ్ సెట్ చేయండి. మీరు ఏ దశలో ఉన్నారో, "
                "తర్వాత ఏమి జరుగుతుందో ఒకే చోట చూడవచ్చు."
            ),
            "cta_set": "పాస్‌వర్డ్ సెట్ చేయండి",
            "signed_in": "మీ దరఖాస్తుల పేజీ నుండి పురోగతిని చూడవచ్చు.",
            "cta_view": "నా దరఖాస్తులు చూడండి",
            "outro": "సమాచారం ఉన్నప్పుడు మేము ఇమెయిల్ చేస్తాము.",
            "expiry": "ఈ లింక్ ఒకసారి మాత్రమే పనిచేస్తుంది, 7 రోజుల్లో ముగుస్తుంది.",
        },
    }
    lang_key = lang if lang in copy else "en"
    c = copy[lang_key]
    lead_html = lead(lang_key, jobe, orge)
    lead_text = lead(lang_key, job, company or "")

    inner = _p(_greeting(lang, name)) + _p(lead_html)
    if set_url:
        inner += _p(c["activate"])
        inner += _button(set_url, c["cta_set"])
        inner += _fallback_link("Or paste this link into your browser:", set_url)
        inner += _p(
            f'<span style="color:{_MUTED};font-size:13px;">{_esc(c["expiry"])}</span>'
        )
    else:
        inner += _p(c["signed_in"])
        inner += _button(apps_url, c["cta_view"])
    inner += _p(c["outro"])

    text_parts = [_greeting(lang, name), "", lead_text]
    if set_url:
        text_parts += ["", c["activate"], set_url, "", c["expiry"]]
    else:
        text_parts += ["", c["signed_in"], apps_url]
    text_parts += ["", c["outro"]]
    return c["subject"], inner, "\n".join(text_parts), lead_text


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


# ===========================================================================
# PH4-A2 — interview loops: the itinerary, the request to pick times, and a
# change to one session. Times arrive pre-formatted in the candidate's
# timezone; a template never guesses a zone.
# ===========================================================================
def _session_lines(sessions: list[dict], lang: str) -> tuple[str, list[str]]:
    """The itinerary as an HTML list and as plain-text lines.

    Each session dict carries pre-formatted strings — ``when`` is already in
    the candidate's timezone (the caller formats it), so the template never
    guesses a zone.
    """
    minutes = {"en": "min", "hi": "मिनट", "te": "నిమిషాలు"}.get(lang, "min")
    html_items: list[str] = []
    text: list[str] = []
    for s in sessions:
        title = _esc(s.get("title", ""))
        when = _esc(s.get("when", ""))
        dur = s.get("duration_minutes")
        place = s.get("location") or ""
        detail = f"{when} · {dur} {minutes}" if dur else when
        extra = f"<br><span style=\"color:#555\">{_esc(place)}</span>" if place else ""
        html_items.append(f"<li style=\"margin:0 0 10px\"><strong>{title}</strong><br>{detail}{extra}</li>")
        text.append(f"- {s.get('title', '')}: {s.get('when', '')}"
                    + (f" ({dur} {minutes})" if dur else "") + (f" — {place}" if place else ""))
    return "<ul style=\"padding-left:18px;margin:8px 0 16px\">" + "".join(html_items) + "</ul>", text


def _t_interview_itinerary(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    org = (ctx.get("brand") or "").strip()
    tz = _esc(ctx.get("timezone", ""))
    updated = bool(ctx.get("updated"))
    cta_url = ctx.get("cta_url")
    jt, orge = _esc(job_title), _esc(org)
    loc = _loc(lang, {
        "en": {
            "subject": f"Your interview schedule for {job_title}",
            "pre": "Your interviews, in one place.",
            "lead": (f"Here is your interview schedule with <strong>{orge}</strong> for "
                     f"<strong>{jt}</strong>." if org
                     else f"Here is your interview schedule for <strong>{jt}</strong>."),
            "tz": f"Times are shown in {tz}.",
            "updated": "Your schedule has changed. This replaces the one we sent before.",
            "cal": "You can add these to your calendar from your applications page.",
            "resched": "If a time does not work for you, reply to the recruiter who contacted you — "
                       "the schedule can only be changed by the hiring team.",
            "cta": "See my applications",
            "outro": "Good luck!",
        },
        "hi": {
            "subject": f"{job_title} के लिए आपका इंटरव्यू शेड्यूल",
            "pre": "आपके सभी इंटरव्यू, एक जगह।",
            "lead": (f"<strong>{orge}</strong> के साथ <strong>{jt}</strong> के लिए आपका इंटरव्यू शेड्यूल यह है।"
                     if org else f"<strong>{jt}</strong> के लिए आपका इंटरव्यू शेड्यूल यह है।"),
            "tz": f"समय {tz} में दिखाया गया है।",
            "updated": "आपका शेड्यूल बदल गया है। यह पहले भेजे गए शेड्यूल की जगह लेता है।",
            "cal": "आप इन्हें अपने आवेदन पेज से अपने कैलेंडर में जोड़ सकते हैं।",
            "resched": "यदि कोई समय आपके लिए उपयुक्त नहीं है, तो उस रिक्रूटर को उत्तर दें जिसने आपसे संपर्क किया — "
                       "शेड्यूल केवल हायरिंग टीम बदल सकती है।",
            "cta": "मेरे आवेदन देखें",
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": f"{job_title} కోసం మీ ఇంటర్వ్యూ షెడ్యూల్",
            "pre": "మీ అన్ని ఇంటర్వ్యూలు, ఒకే చోట.",
            "lead": (f"<strong>{orge}</strong>తో <strong>{jt}</strong> కోసం మీ ఇంటర్వ్యూ షెడ్యూల్ ఇది."
                     if org else f"<strong>{jt}</strong> కోసం మీ ఇంటర్వ్యూ షెడ్యూల్ ఇది."),
            "tz": f"సమయాలు {tz}లో చూపబడ్డాయి.",
            "updated": "మీ షెడ్యూల్ మారింది. ఇది ముందు పంపిన దాని స్థానంలో ఉంటుంది.",
            "cal": "మీ దరఖాస్తుల పేజీ నుండి వీటిని మీ క్యాలెండర్‌కు జోడించవచ్చు.",
            "resched": "ఏదైనా సమయం మీకు అనుకూలం కాకపోతే, మిమ్మల్ని సంప్రదించిన రిక్రూటర్‌కు జవాబు ఇవ్వండి — "
                       "షెడ్యూల్‌ను నియామక బృందం మాత్రమే మార్చగలదు.",
            "cta": "నా దరఖాస్తులు చూడండి",
            "outro": "శుభాకాంక్షలు!",
        },
    })
    items_html, items_text = _session_lines(ctx.get("sessions") or [], lang)
    subject = ("[Updated] " if updated else "") + loc["subject"]
    inner = _p(_greeting(lang, name))
    if updated:
        inner += _p(f"<strong>{loc['updated']}</strong>")
    inner += _p(loc["lead"]) + items_html
    if tz:
        inner += _p(loc["tz"])
    inner += _p(loc["cal"]) + _p(loc["resched"])
    if cta_url:
        inner += _button(cta_url, loc["cta"])
    inner += _p(loc["outro"])
    text = [_greeting(lang, name), ""]
    if updated:
        text.append(loc["updated"])
    text.append(loc["lead"].replace("<strong>", "").replace("</strong>", ""))
    text += items_text
    if tz:
        text.append(loc["tz"])
    text += [loc["cal"], loc["resched"]]
    if cta_url:
        text += ["", cta_url]
    text += ["", loc["outro"]]
    return subject, inner, "\n".join(text), loc["pre"]


def _t_interview_slot_request(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    org = (ctx.get("brand") or "").strip()
    count = int(ctx.get("count") or 1)
    cta_url = ctx.get("cta_url")
    jt, orge = _esc(job_title), _esc(org)
    loc = _loc(lang, {
        "en": {
            "subject": f"Choose your interview times for {job_title}",
            "pre": "Pick the times that suit you.",
            "lead": (f"<strong>{orge}</strong> would like to interview you for <strong>{jt}</strong>."
                     if org else f"You're invited to interview for <strong>{jt}</strong>."),
            "ask": (f"Please choose a time for each of your {count} interviews." if count > 1
                    else "Please choose a time for your interview."),
            "final": "Once you have chosen, the times are confirmed — to change one later, "
                     "contact the hiring team.",
            "cta": "Choose my times",
            "fallback": "Or paste this link into your browser:",
            "outro": "Good luck!",
        },
        "hi": {
            "subject": f"{job_title} के लिए अपने इंटरव्यू का समय चुनें",
            "pre": "अपने लिए सुविधाजनक समय चुनें।",
            "lead": (f"<strong>{orge}</strong> आपका <strong>{jt}</strong> के लिए इंटरव्यू लेना चाहता है।"
                     if org else f"आपको <strong>{jt}</strong> के इंटरव्यू के लिए आमंत्रित किया गया है।"),
            "ask": (f"कृपया अपने {count} इंटरव्यू में से हर एक के लिए समय चुनें।" if count > 1
                    else "कृपया अपने इंटरव्यू के लिए समय चुनें।"),
            "final": "चुनने के बाद समय पक्का हो जाता है — बाद में बदलने के लिए हायरिंग टीम से संपर्क करें।",
            "cta": "मेरा समय चुनें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": f"{job_title} కోసం మీ ఇంటర్వ్యూ సమయాలను ఎంచుకోండి",
            "pre": "మీకు అనుకూలమైన సమయాలను ఎంచుకోండి.",
            "lead": (f"<strong>{orge}</strong> మిమ్మల్ని <strong>{jt}</strong> కోసం ఇంటర్వ్యూ చేయాలనుకుంటోంది."
                     if org else f"మీరు <strong>{jt}</strong> ఇంటర్వ్యూకి ఆహ్వానించబడ్డారు."),
            "ask": (f"దయచేసి మీ {count} ఇంటర్వ్యూలలో ప్రతిదానికి ఒక సమయాన్ని ఎంచుకోండి." if count > 1
                    else "దయచేసి మీ ఇంటర్వ్యూకి ఒక సమయాన్ని ఎంచుకోండి."),
            "final": "ఎంచుకున్న తర్వాత సమయాలు నిర్ధారించబడతాయి — తర్వాత మార్చడానికి నియామక బృందాన్ని సంప్రదించండి.",
            "cta": "నా సమయాలను ఎంచుకోండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(loc["ask"]) + _p(loc["final"])
    if cta_url:
        inner += _button(cta_url, loc["cta"]) + _fallback_link(loc["fallback"], cta_url)
    inner += _p(loc["outro"])
    text = [_greeting(lang, name), "",
            loc["lead"].replace("<strong>", "").replace("</strong>", ""), loc["ask"], loc["final"]]
    if cta_url:
        text += ["", cta_url]
    text += ["", loc["outro"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_interview_session_update(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """One session moved or was cancelled. ``cancelled`` picks the wording."""
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    cancelled = bool(ctx.get("cancelled"))
    session = ctx.get("session") or {}
    cta_url = ctx.get("cta_url")
    jt = _esc(job_title)
    st = _esc(session.get("title", ""))
    loc = _loc(lang, {
        "en": {
            "subject": (f"Interview cancelled — {job_title}" if cancelled
                        else f"Interview time changed — {job_title}"),
            "pre": "An update to your interview schedule.",
            "lead": (f"Your <strong>{st}</strong> interview for <strong>{jt}</strong> has been cancelled."
                     if cancelled
                     else f"Your <strong>{st}</strong> interview for <strong>{jt}</strong> has a new time:"),
            "next": "The hiring team will be in touch about what happens next.",
            "cta": "See my applications",
        },
        "hi": {
            "subject": (f"इंटरव्यू रद्द — {job_title}" if cancelled
                        else f"इंटरव्यू का समय बदला — {job_title}"),
            "pre": "आपके इंटरव्यू शेड्यूल में बदलाव।",
            "lead": (f"<strong>{jt}</strong> के लिए आपका <strong>{st}</strong> इंटरव्यू रद्द कर दिया गया है।"
                     if cancelled
                     else f"<strong>{jt}</strong> के लिए आपके <strong>{st}</strong> इंटरव्यू का नया समय:"),
            "next": "आगे क्या होगा, इसके बारे में हायरिंग टीम आपसे संपर्क करेगी।",
            "cta": "मेरे आवेदन देखें",
        },
        "te": {
            "subject": (f"ఇంటర్వ్యూ రద్దు — {job_title}" if cancelled
                        else f"ఇంటర్వ్యూ సమయం మారింది — {job_title}"),
            "pre": "మీ ఇంటర్వ్యూ షెడ్యూల్‌లో మార్పు.",
            "lead": (f"<strong>{jt}</strong> కోసం మీ <strong>{st}</strong> ఇంటర్వ్యూ రద్దు చేయబడింది."
                     if cancelled
                     else f"<strong>{jt}</strong> కోసం మీ <strong>{st}</strong> ఇంటర్వ్యూకి కొత్త సమయం:"),
            "next": "తదుపరి ఏమి జరుగుతుందో నియామక బృందం మీకు తెలియజేస్తుంది.",
            "cta": "నా దరఖాస్తులు చూడండి",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    text = [_greeting(lang, name), "", loc["lead"].replace("<strong>", "").replace("</strong>", "")]
    if not cancelled:
        items_html, items_text = _session_lines([session], lang)
        inner += items_html
        text += items_text
    else:
        inner += _p(loc["next"])
        text.append(loc["next"])
    if cta_url:
        inner += _button(cta_url, loc["cta"])
        text += ["", cta_url]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_interview_session_reminder(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """A scheduled interview is coming up (24h / 1h). The time is pre-formatted
    in the candidate's timezone by the reminder sweep."""
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    soon = ctx.get("window") == "1h"
    session = ctx.get("session") or {}
    cta_url = ctx.get("cta_url")
    jt, st = _esc(job_title), _esc(session.get("title", ""))
    loc = _loc(lang, {
        "en": {
            "subject": (f"Starting within the hour: your interview for {job_title}" if soon
                        else f"Tomorrow: your interview for {job_title}"),
            "pre": "A reminder about your interview.",
            "lead": f"A reminder: your <strong>{st}</strong> interview for <strong>{jt}</strong> is coming up.",
            "cta": "See my schedule",
        },
        "hi": {
            "subject": (f"एक घंटे के भीतर: {job_title} के लिए आपका इंटरव्यू" if soon
                        else f"कल: {job_title} के लिए आपका इंटरव्यू"),
            "pre": "आपके इंटरव्यू का रिमाइंडर।",
            "lead": f"रिमाइंडर: <strong>{jt}</strong> के लिए आपका <strong>{st}</strong> इंटरव्यू जल्द है।",
            "cta": "मेरा शेड्यूल देखें",
        },
        "te": {
            "subject": (f"ఒక గంటలోపు: {job_title} కోసం మీ ఇంటర్వ్యూ" if soon
                        else f"రేపు: {job_title} కోసం మీ ఇంటర్వ్యూ"),
            "pre": "మీ ఇంటర్వ్యూ గురించి రిమైండర్.",
            "lead": f"రిమైండర్: <strong>{jt}</strong> కోసం మీ <strong>{st}</strong> ఇంటర్వ్యూ త్వరలో ఉంది.",
            "cta": "నా షెడ్యూల్ చూడండి",
        },
    })
    items_html, items_text = _session_lines([session], lang)
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + items_html
    text = [_greeting(lang, name), "", loc["lead"].replace("<strong>", "").replace("</strong>", ""),
            *items_text]
    if cta_url:
        inner += _button(cta_url, loc["cta"])
        text += ["", cta_url]
    return loc["subject"], inner, "\n".join(text), loc["pre"]



# ===========================================================================
# PH4-A3 / A4 — offers and preboarding documents. Every link carries its token
# in the URL fragment; a code is never a link.
# ===========================================================================
def _t_offer_ready(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    org = (ctx.get("company") or ctx.get("brand") or "").strip()
    url = ctx.get("offer_url")
    expires = ctx.get("expires", "")
    resent = bool(ctx.get("resent"))
    # Re-sent after acceptance (to lift a lock, or replace a lost link): the
    # answer is given, so the email is about documents, not a decision.
    accepted = bool(ctx.get("accepted"))
    jt, orge, exp = _esc(job_title), _esc(org), _esc(expires)
    loc = _loc(lang, {
        "en": {
            "subject": f"Your offer from {org}: {job_title}" if org else f"Your offer: {job_title}",
            "pre": "Your offer is ready to read.",
            "lead": (f"<strong>{orge}</strong> is pleased to offer you the role of <strong>{jt}</strong>."
                     if org else f"You have an offer for the role of <strong>{jt}</strong>."),
            "resent": "Here is a fresh link to your offer. The earlier link no longer works.",
            "read": "Read the offer, then accept or decline it. To confirm your answer we will email you a one-time code.",
            "accepted_read": "You have accepted this offer. Use this link to send the documents the hiring team asked for — we will email you a one-time code to open them.",
            "expires": f"The offer is open until <strong>{exp}</strong>.",
            "cta": "Read my offer",
            "fallback": "Or paste this link into your browser:",
            "keep": "This link is personal to you. Please don't forward it.",
        },
        "hi": {
            "subject": f"{org} से आपका ऑफ़र: {job_title}" if org else f"आपका ऑफ़र: {job_title}",
            "pre": "आपका ऑफ़र पढ़ने के लिए तैयार है।",
            "lead": (f"<strong>{orge}</strong> आपको <strong>{jt}</strong> की भूमिका का ऑफ़र देते हुए प्रसन्न है।"
                     if org else f"आपके पास <strong>{jt}</strong> की भूमिका का ऑफ़र है।"),
            "resent": "यह आपके ऑफ़र का नया लिंक है। पुराना लिंक अब काम नहीं करता।",
            "read": "ऑफ़र पढ़ें, फिर उसे स्वीकार या अस्वीकार करें। आपके उत्तर की पुष्टि के लिए हम आपको एक बार उपयोग होने वाला कोड ईमेल करेंगे।",
            "accepted_read": "आपने यह ऑफ़र स्वीकार कर लिया है। हायरिंग टीम द्वारा माँगे गए दस्तावेज़ भेजने के लिए इस लिंक का उपयोग करें — उन्हें खोलने के लिए हम आपको एक बार उपयोग होने वाला कोड ईमेल करेंगे।",
            "expires": f"यह ऑफ़र <strong>{exp}</strong> तक खुला है।",
            "cta": "मेरा ऑफ़र पढ़ें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "keep": "यह लिंक केवल आपके लिए है। कृपया इसे आगे न भेजें।",
        },
        "te": {
            "subject": f"{org} నుండి మీ ఆఫర్: {job_title}" if org else f"మీ ఆఫర్: {job_title}",
            "pre": "మీ ఆఫర్ చదవడానికి సిద్ధంగా ఉంది.",
            "lead": (f"<strong>{orge}</strong> మీకు <strong>{jt}</strong> పాత్రను ఆఫర్ చేయడానికి సంతోషిస్తోంది."
                     if org else f"మీకు <strong>{jt}</strong> పాత్ర కోసం ఆఫర్ ఉంది."),
            "resent": "ఇది మీ ఆఫర్‌కు కొత్త లింక్. పాత లింక్ ఇకపై పనిచేయదు.",
            "read": "ఆఫర్‌ను చదివి, దాన్ని అంగీకరించండి లేదా తిరస్కరించండి. మీ సమాధానాన్ని నిర్ధారించడానికి మేము మీకు ఒకసారి ఉపయోగించే కోడ్‌ను ఇమెయిల్ చేస్తాము.",
            "accepted_read": "మీరు ఈ ఆఫర్‌ను అంగీకరించారు. నియామక బృందం అడిగిన పత్రాలను పంపడానికి ఈ లింక్‌ను ఉపయోగించండి — వాటిని తెరవడానికి మేము మీకు ఒకసారి ఉపయోగించే కోడ్‌ను ఇమెయిల్ చేస్తాము.",
            "expires": f"ఈ ఆఫర్ <strong>{exp}</strong> వరకు తెరిచి ఉంటుంది.",
            "cta": "నా ఆఫర్ చదవండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "keep": "ఈ లింక్ మీకు మాత్రమే. దయచేసి దీన్ని ఫార్వర్డ్ చేయవద్దు.",
        },
    })
    inner = _p(_greeting(lang, name))
    if resent:
        inner += _p(f"<strong>{loc['resent']}</strong>")
    read = loc["accepted_read"] if accepted else loc["read"]
    inner += _p(loc["lead"]) + _p(read)
    if expires and not accepted:
        inner += _p(loc["expires"])
    if url:
        inner += _button(url, loc["cta"]) + _fallback_link(loc["fallback"], url)
    inner += _p(loc["keep"])
    plain = lambda h: h.replace("<strong>", "").replace("</strong>", "")  # noqa: E731
    text = [_greeting(lang, name), ""] + ([loc["resent"]] if resent else []) + [
        plain(loc["lead"]), read] + ([plain(loc["expires"])] if expires and not accepted
                                     else []) + (
        ["", url] if url else []) + ["", loc["keep"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_offer_code(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """A one-time code, worded for what it unlocks: accepting, declining, or
    opening the documents step. (The documents code once fell through to the
    decline wording — a candidate told they were confirming a refusal.)"""
    name = ctx.get("name")
    code = str(ctx.get("code", ""))
    minutes = int(ctx.get("minutes") or 10)
    purpose = ctx.get("purpose")
    purpose = purpose if purpose in ("accept", "decline", "documents") else "decline"
    words = {
        "en": {"accept": ("Your code to accept the offer", "Use this code to accept your offer:"),
               "decline": ("Your code to decline the offer", "Use this code to decline your offer:"),
               "documents": ("Your code to open your documents",
                             "Use this code to open the documents step of your offer:")},
        "hi": {"accept": ("ऑफ़र स्वीकार करने के लिए आपका कोड",
                          "अपना ऑफ़र स्वीकार करने के लिए इस कोड का उपयोग करें:"),
               "decline": ("ऑफ़र अस्वीकार करने के लिए आपका कोड",
                           "अपना ऑफ़र अस्वीकार करने के लिए इस कोड का उपयोग करें:"),
               "documents": ("अपने दस्तावेज़ खोलने के लिए आपका कोड",
                             "अपने ऑफ़र का दस्तावेज़ चरण खोलने के लिए इस कोड का उपयोग करें:")},
        "te": {"accept": ("ఆఫర్‌ను అంగీకరించడానికి మీ కోడ్",
                          "మీ ఆఫర్‌ను అంగీకరించడానికి ఈ కోడ్‌ను ఉపయోగించండి:"),
               "decline": ("ఆఫర్‌ను తిరస్కరించడానికి మీ కోడ్",
                           "మీ ఆఫర్‌ను తిరస్కరించడానికి ఈ కోడ్‌ను ఉపయోగించండి:"),
               "documents": ("మీ పత్రాలను తెరవడానికి మీ కోడ్",
                             "మీ ఆఫర్ యొక్క పత్రాల దశను తెరవడానికి ఈ కోడ్‌ను ఉపయోగించండి:")},
    }
    loc = _loc(lang, {
        "en": {
            "subject": words["en"][purpose][0], "pre": "Your one-time code.",
            "lead": words["en"][purpose][1],
            "exp": f"It works once, for {minutes} minutes.",
            "not_you": "If you did not ask for this code, you can ignore this email — nothing happens without it.",
        },
        "hi": {
            "subject": words["hi"][purpose][0], "pre": "आपका एक बार उपयोग होने वाला कोड।",
            "lead": words["hi"][purpose][1],
            "exp": f"यह केवल एक बार, {minutes} मिनट के लिए काम करता है।",
            "not_you": "यदि आपने यह कोड नहीं मांगा, तो इस ईमेल को अनदेखा करें — इसके बिना कुछ नहीं होता।",
        },
        "te": {
            "subject": words["te"][purpose][0], "pre": "మీ ఒకసారి ఉపయోగించే కోడ్.",
            "lead": words["te"][purpose][1],
            "exp": f"ఇది ఒక్కసారి మాత్రమే, {minutes} నిమిషాల పాటు పనిచేస్తుంది.",
            "not_you": "మీరు ఈ కోడ్ అడగకపోతే, ఈ ఇమెయిల్‌ను విస్మరించండి — ఇది లేకుండా ఏమీ జరగదు.",
        },
    })
    big = (f'<p style="font-size:28px;letter-spacing:6px;font-weight:700;margin:12px 0 18px;">'
           f"{_esc(code)}</p>")
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + big + _p(loc["exp"]) + _p(loc["not_you"])
    text = [_greeting(lang, name), "", loc["lead"], code, "", loc["exp"], loc["not_you"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_offer_update(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """The offer was withdrawn by the company."""
    name = ctx.get("name")
    job_title = ctx.get("job_title", "the role")
    org = (ctx.get("company") or "").strip()
    jt, orge = _esc(job_title), _esc(org)
    loc = _loc(lang, {
        "en": {"subject": f"Update on your offer: {job_title}",
               "pre": "An update on your offer.",
               "lead": (f"<strong>{orge}</strong> has withdrawn its offer for <strong>{jt}</strong>."
                        if org else f"The offer for <strong>{jt}</strong> has been withdrawn."),
               "next": "If you have questions, reply to the recruiter who contacted you."},
        "hi": {"subject": f"आपके ऑफ़र पर अपडेट: {job_title}",
               "pre": "आपके ऑफ़र पर एक अपडेट।",
               "lead": (f"<strong>{orge}</strong> ने <strong>{jt}</strong> के लिए अपना ऑफ़र वापस ले लिया है।"
                        if org else f"<strong>{jt}</strong> का ऑफ़र वापस ले लिया गया है।"),
               "next": "यदि आपके कोई प्रश्न हैं, तो उस रिक्रूटर को उत्तर दें जिसने आपसे संपर्क किया।"},
        "te": {"subject": f"మీ ఆఫర్‌పై అప్‌డేట్: {job_title}",
               "pre": "మీ ఆఫర్‌పై ఒక అప్‌డేట్.",
               "lead": (f"<strong>{orge}</strong> <strong>{jt}</strong> కోసం తన ఆఫర్‌ను ఉపసంహరించుకుంది."
                        if org else f"<strong>{jt}</strong> కోసం ఆఫర్ ఉపసంహరించబడింది."),
               "next": "మీకు ప్రశ్నలు ఉంటే, మిమ్మల్ని సంప్రదించిన రిక్రూటర్‌కు జవాబు ఇవ్వండి."},
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(loc["next"])
    text = [_greeting(lang, name), "", loc["lead"].replace("<strong>", "").replace("</strong>", ""),
            loc["next"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_document_update(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """HR rejected a preboarding document, or asked for a replacement."""
    name = ctx.get("name")
    doc = _esc(ctx.get("document", ""))
    reason = ctx.get("reason")
    loc = _loc(lang, {
        "en": {"subject": "Please upload a document again",
               "pre": "One of your documents needs attention.",
               "lead": f"The hiring team needs a new copy of <strong>{doc}</strong>.",
               "why": "What they said:",
               "how": "Open your offer link and upload a new file for this document."},
        "hi": {"subject": "कृपया एक दस्तावेज़ फिर से अपलोड करें",
               "pre": "आपके एक दस्तावेज़ पर ध्यान देने की ज़रूरत है।",
               "lead": f"हायरिंग टीम को <strong>{doc}</strong> की नई प्रति चाहिए।",
               "why": "उन्होंने क्या कहा:",
               "how": "अपना ऑफ़र लिंक खोलें और इस दस्तावेज़ के लिए नई फ़ाइल अपलोड करें।"},
        "te": {"subject": "దయచేసి ఒక పత్రాన్ని మళ్లీ అప్‌లోడ్ చేయండి",
               "pre": "మీ పత్రాల్లో ఒకదానికి శ్రద్ధ అవసరం.",
               "lead": f"నియామక బృందానికి <strong>{doc}</strong> యొక్క కొత్త కాపీ అవసరం.",
               "why": "వారు చెప్పింది:",
               "how": "మీ ఆఫర్ లింక్‌ను తెరిచి, ఈ పత్రం కోసం కొత్త ఫైల్‌ను అప్‌లోడ్ చేయండి."},
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    text = [_greeting(lang, name), "", loc["lead"].replace("<strong>", "").replace("</strong>", "")]
    if reason:
        inner += _p(f"<strong>{loc['why']}</strong> {_esc(reason)}")
        text.append(f"{loc['why']} {reason}")
    inner += _p(loc["how"])
    text.append(loc["how"])
    return loc["subject"], inner, "\n".join(text), loc["pre"]




def _t_document_received(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """A document arrived through the candidate's offer link — told to them, so
    one they did not send is noticed."""
    name = ctx.get("name")
    doc = _esc(ctx.get("document", ""))
    loc = _loc(lang, {
        "en": {"subject": "We received your document",
               "pre": "A document was uploaded to your offer.",
               "lead": f"We received <strong>{doc}</strong>. The hiring team will review it.",
               "not_you": "If you did not upload this, contact the hiring team straight away."},
        "hi": {"subject": "हमें आपका दस्तावेज़ मिल गया",
               "pre": "आपके ऑफ़र पर एक दस्तावेज़ अपलोड किया गया।",
               "lead": f"हमें <strong>{doc}</strong> मिल गया। हायरिंग टीम इसकी समीक्षा करेगी।",
               "not_you": "यदि आपने इसे अपलोड नहीं किया, तो तुरंत हायरिंग टीम से संपर्क करें।"},
        "te": {"subject": "మీ పత్రం మాకు అందింది",
               "pre": "మీ ఆఫర్‌కు ఒక పత్రం అప్‌లోడ్ చేయబడింది.",
               "lead": f"మాకు <strong>{doc}</strong> అందింది. నియామక బృందం దాన్ని సమీక్షిస్తుంది.",
               "not_you": "మీరు దీన్ని అప్‌లోడ్ చేయకపోతే, వెంటనే నియామక బృందాన్ని సంప్రదించండి."},
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(f"<strong>{loc['not_you']}</strong>")
    text = [_greeting(lang, name), "", loc["lead"].replace("<strong>", "").replace("</strong>", ""),
            loc["not_you"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]



def _t_offer_account(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """After accepting an offer with no account yet: claim the one made for them.

    ctx: name, job_title, company, set_url (optional), applications_url — the
    same two shapes as application_received (PH4-A3, security review L-new).
    """
    name = ctx.get("name")
    job = ctx.get("job_title") or "the role"
    company = ctx.get("company") or ""
    set_url = ctx.get("set_url")
    apps_url = ctx.get("applications_url") or settings.app_base_url
    jobe, orge = _esc(job), _esc(company)
    loc = _loc(lang, {
        "en": {"subject": f"Welcome aboard — set up your account for {job}",
               "lead": (f"Thank you for accepting the role of <strong>{jobe}</strong>"
                        + (f" at <strong>{orge}</strong>." if company else ".")),
               "activate": "Set a password to follow your onboarding and see your offer in one place.",
               "cta_set": "Set my password",
               "signed_in": "You can see your offer and onboarding from your applications page.",
               "cta_view": "View my applications",
               "expiry": "This link can be used once and expires in 7 days.",
               "fallback": "Or paste this link into your browser:"},
        "hi": {"subject": f"स्वागत है — {job} के लिए अपना खाता सेट करें",
               "lead": ((f"<strong>{orge}</strong> में " if company else "")
                        + f"<strong>{jobe}</strong> की भूमिका स्वीकार करने के लिए धन्यवाद।"),
               "activate": "अपनी ऑनबोर्डिंग देखने और अपना ऑफ़र एक ही जगह देखने के लिए पासवर्ड सेट करें।",
               "cta_set": "पासवर्ड सेट करें",
               "signed_in": "आप अपने आवेदन पृष्ठ से अपना ऑफ़र और ऑनबोर्डिंग देख सकते हैं।",
               "cta_view": "मेरे आवेदन देखें",
               "expiry": "यह लिंक एक बार उपयोग हो सकता है और 7 दिनों में समाप्त हो जाएगा।",
               "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:"},
        "te": {"subject": f"స్వాగతం — {job} కోసం మీ ఖాతాను సెటప్ చేయండి",
               "lead": ((f"<strong>{orge}</strong>లో " if company else "")
                        + f"<strong>{jobe}</strong> పాత్రను అంగీకరించినందుకు ధన్యవాదాలు."),
               "activate": "మీ ఆన్‌బోర్డింగ్‌ను, మీ ఆఫర్‌ను ఒకే చోట చూడటానికి పాస్‌వర్డ్ సెట్ చేయండి.",
               "cta_set": "పాస్‌వర్డ్ సెట్ చేయండి",
               "signed_in": "మీ దరఖాస్తుల పేజీ నుండి మీ ఆఫర్‌ను, ఆన్‌బోర్డింగ్‌ను చూడవచ్చు.",
               "cta_view": "నా దరఖాస్తులు చూడండి",
               "expiry": "ఈ లింక్ ఒకసారి మాత్రమే పనిచేస్తుంది, 7 రోజుల్లో ముగుస్తుంది.",
               "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:"},
    })
    plain = loc["lead"].replace("<strong>", "").replace("</strong>", "")
    inner = _p(_greeting(lang, name)) + _p(loc["lead"])
    text = [_greeting(lang, name), "", plain]
    if set_url:
        inner += _p(loc["activate"]) + _button(set_url, loc["cta_set"])
        inner += _fallback_link(loc["fallback"], set_url)
        inner += _p(f'<span style="color:{_MUTED};font-size:13px;">{_esc(loc["expiry"])}</span>')
        text += ["", loc["activate"], set_url, "", loc["expiry"]]
    else:
        inner += _p(loc["signed_in"]) + _button(apps_url, loc["cta_view"])
        text += ["", loc["signed_in"], apps_url]
    return loc["subject"], inner, "\n".join(text), plain


def _t_accommodation_recorded(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """PH4-D2: an accommodation was recorded for the candidate's application.

    ctx: name, extra_time_percent (int | None), deadline_extension_days
    (int | None), relax_auto_submit (bool), has_other_adjustment (bool).
    NEVER ``other_adjustment``'s text, ``interviewer_note`` or
    ``internal_note`` — this email states only WHICH parameters were
    recorded, never any note."""
    name = ctx.get("name")
    pct = ctx.get("extra_time_percent")
    days = ctx.get("deadline_extension_days")
    relaxed = bool(ctx.get("relax_auto_submit"))
    has_other = bool(ctx.get("has_other_adjustment"))
    loc = _loc(lang, {
        "en": {
            "subject": "An adjustment was recorded for your application",
            "pre": "A hiring adjustment was recorded for you.",
            "lead": "The hiring team recorded the following for your application:",
            "extra_time": f"{pct}% extra time on timed assessments",
            "extra_days": f"{days} extra day(s) to complete assessments",
            "relaxed": "Assessments will not end early for proctoring flags",
            "other": "Other adjustments recorded for your application",
            "closing": "If anything here looks wrong, reply to the hiring team.",
        },
        "hi": {
            "subject": "आपके आवेदन के लिए एक समायोजन दर्ज किया गया",
            "pre": "आपके लिए एक भर्ती समायोजन दर्ज किया गया।",
            "lead": "हायरिंग टीम ने आपके आवेदन के लिए निम्नलिखित दर्ज किया:",
            "extra_time": f"समयबद्ध मूल्यांकन में {pct}% अतिरिक्त समय",
            "extra_days": f"मूल्यांकन पूरा करने के लिए {days} अतिरिक्त दिन",
            "relaxed": "प्रॉक्टरिंग फ़्लैग के कारण मूल्यांकन जल्दी समाप्त नहीं होगा",
            "other": "आपके आवेदन के लिए अन्य समायोजन दर्ज किए गए",
            "closing": "यदि यहाँ कुछ गलत लगे, तो हायरिंग टीम को उत्तर दें।",
        },
        "te": {
            "subject": "మీ దరఖాస్తు కోసం ఒక సర్దుబాటు నమోదు చేయబడింది",
            "pre": "మీ కోసం ఒక నియామక సర్దుబాటు నమోదు చేయబడింది.",
            "lead": "మీ దరఖాస్తు కోసం నియామక బృందం ఈ క్రిందివి నమోదు చేసింది:",
            "extra_time": f"సమయ-పరిమిత మూల్యాంకనాల్లో {pct}% అదనపు సమయం",
            "extra_days": f"మూల్యాంకనాలు పూర్తి చేయడానికి {days} అదనపు రోజు(లు)",
            "relaxed": "ప్రొక్టరింగ్ ఫ్లాగ్‌ల వల్ల మూల్యాంకనం ముందుగా ముగియదు",
            "other": "మీ దరఖాస్తు కోసం ఇతర సర్దుబాట్లు నమోదు చేయబడ్డాయి",
            "closing": "ఇక్కడ ఏదైనా తప్పుగా అనిపిస్తే, నియామక బృందానికి జవాబు ఇవ్వండి.",
        },
    })
    items: list[str] = []
    if pct:
        items.append(loc["extra_time"])
    if days:
        items.append(loc["extra_days"])
    if relaxed:
        items.append(loc["relaxed"])
    if has_other:
        items.append(loc["other"])
    list_html = "".join(f'<li style="margin:4px 0;">{_esc(i)}</li>' for i in items)
    inner = (
        _p(_greeting(lang, name))
        + _p(loc["lead"])
        + f'<ul style="margin:0 0 16px;padding-left:20px;">{list_html}</ul>'
        + _p(loc["closing"])
    )
    text = [_greeting(lang, name), "", loc["lead"], *[f"- {i}" for i in items], "", loc["closing"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_task_assigned(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """PH4-D4: a job simulation or portfolio round was issued.

    ctx: name, round_title, kind ('job_simulation'|'portfolio'), task_url, due.
    """
    name = ctx.get("name")
    round_title = ctx.get("round_title", "")
    due = ctx.get("due")
    task_url = ctx["task_url"]
    is_portfolio = ctx.get("kind") == "portfolio"
    ttle = _esc(round_title)
    loc = _loc(lang, {
        "en": {
            "subject": f"Your task: {round_title}" if round_title else "Your task is ready",
            "pre": "A task is waiting for you.",
            "lead": (
                (f"You've been asked to complete <strong>{ttle}</strong>." if round_title
                 else "You've been asked to complete a task.")
                + (" Share a portfolio of your work — files or approved links."
                   if is_portfolio else " Work through it in your own time, within the window.")
            ),
            "cta": "Open the task",
            "fallback": "Or paste this link into your browser:",
            "due": "Due by:",
            "outro": "All the best!",
        },
        "hi": {
            "subject": f"आपका टास्क: {round_title}" if round_title else "आपका टास्क तैयार है",
            "pre": "आपके लिए एक टास्क तैयार है।",
            "lead": (
                (f"आपसे <strong>{ttle}</strong> पूरा करने के लिए कहा गया है।" if round_title
                 else "आपसे एक टास्क पूरा करने के लिए कहा गया है।")
                + (" अपने काम का पोर्टफोलियो साझा करें — फ़ाइलें या स्वीकृत लिंक।"
                   if is_portfolio else " अपने समय पर, दी गई अवधि के भीतर इसे पूरा करें।")
            ),
            "cta": "टास्क खोलें",
            "fallback": "या यह लिंक अपने ब्राउज़र में पेस्ट करें:",
            "due": "अंतिम तिथि:",
            "outro": "शुभकामनाएँ!",
        },
        "te": {
            "subject": f"మీ టాస్క్: {round_title}" if round_title else "మీ టాస్క్ సిద్ధంగా ఉంది",
            "pre": "మీ కోసం ఒక టాస్క్ సిద్ధంగా ఉంది.",
            "lead": (
                (f"మీరు <strong>{ttle}</strong> పూర్తి చేయమని అడగబడ్డారు." if round_title
                 else "మీరు ఒక టాస్క్ పూర్తి చేయమని అడగబడ్డారు.")
                + (" మీ పని పోర్ట్‌ఫోలియోను పంచుకోండి — ఫైళ్లు లేదా ఆమోదించిన లింక్‌లు."
                   if is_portfolio else " మీ సమయంలో, ఇచ్చిన వ్యవధిలో దీన్ని పూర్తి చేయండి.")
            ),
            "cta": "టాస్క్ తెరవండి",
            "fallback": "లేదా ఈ లింక్‌ను మీ బ్రౌజర్‌లో పేస్ట్ చేయండి:",
            "due": "గడువు:",
            "outro": "శుభాకాంక్షలు!",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _button(task_url, loc["cta"])
    inner += _fallback_link(loc["fallback"], task_url)
    if due:
        inner += _p(f'<span style="color:{_MUTED};font-size:13px;">'
                    f'<strong>{_esc(loc["due"])}</strong> {_esc(due)}</span>')
    inner += _p(loc["outro"])
    text = [_greeting(lang, name), "",
            html_lib.unescape(loc["lead"].replace("<strong>", "").replace("</strong>", "")),
            "", task_url]
    if due:
        text += ["", f"{loc['due']} {due}"]
    text += ["", loc["outro"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


def _t_task_received(lang: str, ctx: dict) -> tuple[str, str, str, str]:
    """PH4-D4: the candidate submitted their task. ctx: name, round_title."""
    name = ctx.get("name")
    round_title = ctx.get("round_title", "")
    ttle = _esc(round_title)
    loc = _loc(lang, {
        "en": {
            "subject": "We received your submission",
            "pre": "Your task has been submitted.",
            "lead": (f"We received your submission for <strong>{ttle}</strong>. The hiring "
                     "team will review it." if round_title else
                     "We received your submission. The hiring team will review it."),
            "outro": "Thank you for your work on this.",
        },
        "hi": {
            "subject": "हमें आपका सबमिशन मिल गया",
            "pre": "आपका टास्क सबमिट हो गया है।",
            "lead": (f"हमें <strong>{ttle}</strong> के लिए आपका सबमिशन मिल गया। हायरिंग टीम "
                     "इसकी समीक्षा करेगी।" if round_title else
                     "हमें आपका सबमिशन मिल गया। हायरिंग टीम इसकी समीक्षा करेगी।"),
            "outro": "इस पर आपके काम के लिए धन्यवाद।",
        },
        "te": {
            "subject": "మీ సమర్పణ మాకు అందింది",
            "pre": "మీ టాస్క్ సమర్పించబడింది.",
            "lead": (f"<strong>{ttle}</strong> కోసం మీ సమర్పణ మాకు అందింది. నియామక బృందం దాన్ని "
                     "సమీక్షిస్తుంది." if round_title else
                     "మీ సమర్పణ మాకు అందింది. నియామక బృందం దాన్ని సమీక్షిస్తుంది."),
            "outro": "దీనిపై మీ కృషికి ధన్యవాదాలు.",
        },
    })
    inner = _p(_greeting(lang, name)) + _p(loc["lead"]) + _p(loc["outro"])
    text = [_greeting(lang, name), "", loc["lead"], "", loc["outro"]]
    return loc["subject"], inner, "\n".join(text), loc["pre"]


_BUILDERS = {
    "welcome": _t_welcome,
    "email_verify": _t_email_verify,
    "password_reset": _t_password_reset,
    "login_alert": _t_login_alert,
    "exam_link": _t_exam_link,
    "interview_invite": _t_interview_invite,
    "hr_credentials": _t_hr_credentials,
    "application_received": _t_application_received,
    "decision": _t_decision,
    # A2/A3 — the five candidate lifecycle emails, each its own template:
    # reminder (exam / interview), expiry warning, no-show follow-up, results
    # ready. 'link_expired' is the notice once a link has actually lapsed.
    "exam_reminder": _t_exam_reminder,
    "interview_reminder": _t_interview_reminder,
    "link_expiring": _t_link_expiring,
    "interview_no_show": _t_interview_no_show,
    "link_expired": _t_link_expired,
    "results_ready": _t_results_ready,
    # PH4-A2 interview loops.
    "interview_itinerary": _t_interview_itinerary,
    "interview_slot_request": _t_interview_slot_request,
    "interview_session_update": _t_interview_session_update,
    "interview_session_reminder": _t_interview_session_reminder,
    # PH4-A3 / A4 offers and preboarding documents.
    "offer_ready": _t_offer_ready,
    "offer_code": _t_offer_code,
    "offer_update": _t_offer_update,
    "document_update": _t_document_update,
    "document_received": _t_document_received,
    "offer_account": _t_offer_account,
    # PH4-D2 — candidate accommodations.
    "accommodation_recorded": _t_accommodation_recorded,
    # PH4-D4 — job simulations and portfolio rounds.
    "task_assigned": _t_task_assigned,
    "task_received": _t_task_received,
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
