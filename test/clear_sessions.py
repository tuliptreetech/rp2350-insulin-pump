"""
Stop every session on the server.

Run once before a parallel test run, never from inside a test: a leaked
session from a crashed run silently stalls the next session on the same
project, but clearing them mid-run would kill sibling workers' sessions too.
"""

import os

from emerson import EmulatorController

HOST = os.environ.get("EMERSON_HOST", "http://localhost:10314")

with EmulatorController(HOST).connect() as conn:
    sessions = conn.get_instance_list()
    for session_id, name in sessions:
        conn.stop_project(session_id)
        print(f"stopped leftover session {session_id} ({name})")
    print(f"{len(sessions)} session(s) cleared")
