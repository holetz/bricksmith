from bricksmith.quality import Check, require_check
from bricksmith.tasks import task


@task(kind="check")
def check_customer_features(ctx):
    """Gate: every feature row has a customer."""
    require_check(Check.not_null("customer_id"), ctx.read("customer_features"))
