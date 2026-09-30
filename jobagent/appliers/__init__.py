from .base import Applier, ApplyResult
from .indeed import IndeedApplier
from .linkedin import LinkedInApplier
from .ziprecruiter import ZipRecruiterApplier

APPLIERS: dict[str, type[Applier]] = {
    "linkedin": LinkedInApplier,
    "indeed": IndeedApplier,
    "zip_recruiter": ZipRecruiterApplier,
}

__all__ = ["APPLIERS", "Applier", "ApplyResult"]
