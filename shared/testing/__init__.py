"""Test-support helpers shared by the four services' test suites.

Nothing in here is imported by application code. It lives under ``shared/`` only
so that every service's tests can import one implementation instead of four
drifting copies (the failure mode ``services/feedback_billing/app/auth.py``
documents for its own helper).
"""
