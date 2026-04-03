"""
eLabFTW API client for BMD ELN logger.
Handles creating, updating entries and uploading files.
"""

import os
import json
import requests
import urllib3
from pathlib import Path

# Suppress SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# CONFIG — edit these or set as env variables
# ─────────────────────────────────────────────

ELABFTW_URL     = os.environ.get("ELABFTW_URL", "https://elab.bmdgroup.lmu.de")
ELABFTW_TOKEN   = os.environ.get("ELABFTW_TOKEN", "")   # per-user API token
VERIFY_SSL      = False  # set False for self-signed certs on local install

# ─────────────────────────────────────────────
# eLabFTW v5 status IDs (team 1 / Default team)
# ─────────────────────────────────────────────
STATUS_RUNNING  = 1   # "Running"
STATUS_SUCCESS  = 2   # "Success"
STATUS_REDO     = 3   # "Need to be redone"
STATUS_FAIL     = 4   # "Fail"


class ElabFTWClient:
    """Minimal eLabFTW REST API client."""

    def __init__(self, url: str = ELABFTW_URL, token: str = ELABFTW_TOKEN,
                 verify_ssl: bool = VERIFY_SSL):
        self.base = url.rstrip("/") + "/api/v2"
        self.headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.verify = verify_ssl

    def _req(self, method, path, **kwargs):
        url = f"{self.base}/{path.lstrip('/')}"
        resp = getattr(requests, method)(
            url, headers=self.headers, verify=self.verify, **kwargs)
        if not resp.ok:
            raise RuntimeError(
                f"eLabFTW API error {resp.status_code}: {resp.text[:300]}")
        return resp

    # ── Experiments ──────────────────────────────

    def create_experiment(self, title: str, body: str,
                           tags: list = None,
                           metadata: dict = None) -> int:
        """
        Create a new experiment entry. Returns the new experiment ID.
        """
        payload = {
            "title":     title,
            "body":      body,
            "status_id": STATUS_RUNNING,
        }
        if metadata:
            payload["metadata"] = json.dumps({"extra_fields": metadata})

        resp = self._req("post", "/experiments", json=payload)
        # eLabFTW returns the new ID in the Location header
        location = resp.headers.get("Location", "")
        exp_id = int(location.rstrip("/").split("/")[-1])

        if tags:
            for tag in tags:
                self._req("post", f"/experiments/{exp_id}/tags",
                          json={"tag": tag})

        return exp_id

    def update_experiment(self, exp_id: int, title: str = None,
                           body: str = None, status_id: int = None,
                           metadata: dict = None):
        """Update fields on an existing experiment."""
        payload = {}
        if title:     payload["title"]     = title
        if body:      payload["body"]      = body
        if status_id: payload["status_id"] = status_id
        if metadata:  payload["metadata"]  = json.dumps({"extra_fields": metadata})
        self._req("patch", f"/experiments/{exp_id}", json=payload)

    def upload_file(self, exp_id: int, file_path: str, comment: str = ""):
        """Attach a file to an experiment entry."""
        p = Path(file_path)
        if not p.exists():
            return
        headers = {k: v for k, v in self.headers.items()
                   if k != "Content-Type"}   # let requests set multipart boundary
        with open(p, "rb") as f:
            requests.post(
                f"{self.base}/experiments/{exp_id}/uploads",
                headers=headers,
                files={"file": (p.name, f)},
                data={"comment": comment},
                verify=self.verify,
            )

    def add_comment(self, exp_id: int, comment: str):
        """Add a comment to an experiment."""
        self._req("post", f"/experiments/{exp_id}/comments",
                  json={"comment": comment})

    # ── Status helpers ────────────────────────────

    def mark_submitted(self, exp_id: int, slurm_job_id: str):
        self.update_experiment(
            exp_id,
            status_id=STATUS_RUNNING,
            metadata={"slurm_job_id": {"value": slurm_job_id, "type": "text"}}
        )

    def mark_completed(self, exp_id: int, success: bool):
        self.update_experiment(
            exp_id,
            status_id=STATUS_SUCCESS if success else STATUS_FAIL
        )

    def mark_non_cp2k(self, exp_id: int, detected_code: str):
        """Flag that a non-CP2K job was detected — partial logging only."""
        msg = (f"<p style='color:red'>⚠️ <b>Non-CP2K job detected "
               f"({detected_code})</b>. Full metadata parsing is not yet "
               f"supported for this code. Manual annotation required.</p>")
        self.update_experiment(exp_id, body=msg, status_id=STATUS_REDO)


if __name__ == "__main__":
    # Quick connectivity test
    client = ElabFTWClient()
    print(f"Connecting to: {client.base}")
    try:
        client._req("get", "/experiments?limit=1")
        print("✅ Connection OK")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
