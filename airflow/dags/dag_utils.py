# Shared utilities for all DAGs

from airflow.models import Variable  # Airflow Variable: key-value store editable in UI → Admin → Variables
from airflow.exceptions import AirflowSkipException  # signals Airflow to mark the task as Skipped (not failed)


def check_vacation_mode() -> None:
    """
    ### Vacation Mode Guard

    Raises AirflowSkipException if the `VACATION_MODE` Airflow Variable is set
    to `"true"`, halting the task (and all downstream tasks) without failing the run.

    #### How to enable (before leaving):
    1. Airflow UI → Admin → Variables → "+" → Key: `VACATION_MODE`, Value: `true`
    2. Also pause both DAGs in the Airflow UI (belt-and-suspenders)

    #### How to disable (when you return):
    - Airflow UI → Admin → Variables → set `VACATION_MODE` to `false` (or delete it)
    - Then unpause both DAGs

    #### Why Airflow Variable instead of an env var?
    - Changeable via the Airflow UI — no SSH, no kubectl, no laptop required
    - Persists in the Airflow metadata DB alongside pause state
    - Defaults to "false" if the variable doesn't exist — no changes needed for normal operation
    """
    # Fetch VACATION_MODE from Airflow Variables; treat missing variable as "false"
    if Variable.get("VACATION_MODE", default_var="false").lower() == "true":
        raise AirflowSkipException("VACATION_MODE is enabled — skipping all API calls for this run.")
