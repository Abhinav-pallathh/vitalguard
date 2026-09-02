"""VitalGuard -- personalized, context-aware, signal-honest vitals monitoring.

Architecture note, and it is the one that matters:

    The scorer PROPOSES a context. A deterministic gate CONCLUDES.

No inferred number is ever shown to a user without passing the quality gate
first. A reading we do not trust is reported as UNSCORED -- never as a value,
never as a guess, never as the last good number held over. For a device that
touches cardiac risk and independent living, that is the only defensible
design.
"""
__version__ = "0.1.0"
