"""Human review is required for public release, independently of schema validity."""
from .core import PipelineError
from .omegat import iter_translation_units
from .review_status import calculate_status, load_ledger


def require_reviewed(project):
    version = str(project.manifest["document"]["version"])
    date = str(project.manifest["document"]["effectiveDate"])
    ledger = load_ledger(project.root / "review" / f"mtr-{date}.json", version)
    statuses = calculate_status(list(iter_translation_units(project)), ledger)
    pending = [item.unit_id for item in statuses if not item.status.startswith("reviewed-")]
    if pending:
        raise PipelineError(f"正式发布需要全文有效审校；尚有 {len(pending)} 个未审或失效单元：" + ", ".join(pending[:5]))
