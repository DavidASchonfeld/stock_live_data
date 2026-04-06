import os
from datetime import datetime
from pprint import pprint
from pprint import pformat

from constants import outputTextsFolder_folderPath  # My constants Python file is in .gitignore


# ── Why a custom logger instead of Python's built-in logging module? ──────────
# Airflow already captures stdout/stderr and shows them in the Task Logs tab of
# the Airflow UI. But those logs live inside the pod and are only accessible via
# the web UI — you can't just SSH in and cat a file.
#
# OutputTextWriter writes to a Kubernetes PersistentVolumeClaim (PVC) that is
# mounted at /opt/airflow/out inside the pod AND at a path on the EC2 host.
# This lets you read the log files directly on the EC2 instance without opening
# the Airflow UI, which is useful during development and debugging.
#
# Each instance creates a NEW timestamped .txt file (e.g. "2025-08-02 17:35:13.txt")
# so every DAG run produces its own log file — easy to correlate with Airflow's
# run timestamps. Multiple tasks in the same DAG each get their own writer
# (and therefore their own file) because each writer is instantiated inside its
# own @task function.
# ─────────────────────────────────────────────────────────────────────────────


class OutputTextWriter:

    outputTextFileName : str

    def __init__(self, inPath : str = outputTextsFolder_folderPath):
        # Soft-fail if PVC isn't mounted or writable — file logging is debug-only, never crash the task
        if not os.access(inPath, os.W_OK):
            print(f"WARNING: OutputTextWriter cannot write to '{inPath}' (PVC not mounted or wrong permissions) — stdout only.")
            self._file_enabled = False  # flag used by log() to skip file writes
            self.outputTextFileName = None
            return
        self._file_enabled = True  # PVC is writable; file logging is active
        # Filename = current timestamp so each DAG task run gets its own log file
        self.outputTextFileName : str = os.path.join(inPath, str(datetime.now())+".txt")

    def log(self, inString: str) -> str:  # renamed from print() — avoids shadowing Python's built-in print function
        # Always write to stdout; only write to file if PVC is mounted and writable
        print(inString)
        if self._file_enabled:
            with open(self.outputTextFileName, "a") as textFile:
                textFile.write("\n"+inString)
        return inString

    def print_dict(self, inDict: dict, prettyPrint : bool = False) -> str:
        # prettyPrint=True uses pprint for human-readable indented output; False for compact single-line
        ## Pretty Print
        if (prettyPrint):

            ## Terminal
            pprint(inDict)

            ## Only write to file if PVC is active
            if self._file_enabled:
                with open(self.outputTextFileName, "a") as textFile:
                    pprint(inDict, stream=textFile)

            return pformat(inDict, indent = 4)
    

        ## Regular String Printing (aka non-Pretty Print)
        else: ## Not Pretty Print
            return self.log(str(inDict))  # delegate to log() — self.print() was renamed to self.log()