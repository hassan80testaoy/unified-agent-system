"""Local plaintext Excel account ledger (`data/accounts.xlsx`) — NEVER committed or logged (§0C.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.config import get_settings

SHEET_HEADERS = ["site", "email_username", "password", "profile_url", "created_at", "notes"]


def get_accounts_spreadsheet_path() -> Path:
    data_dir = get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "accounts.xlsx"


def record_account_in_spreadsheet(
    *,
    domain: str,
    email_username: str,
    password: str,
    profile_url: str = "",
    notes: str = "",
    created_at: str | None = None,
) -> Path:
    """Create or upsert one account row in local `data/accounts.xlsx` without duplicating `(site, email)`."""
    path = get_accounts_spreadsheet_path()
    norm_site = (domain or "").strip().lower()
    norm_user = (email_username or "").strip()
    ts = created_at or datetime.now(timezone.utc).isoformat()

    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
        # Ensure header row if empty
        if ws.max_row < 1 or ws.cell(row=1, column=1).value != "site":
            ws.insert_rows(1)
            for idx, col_name in enumerate(SHEET_HEADERS, start=1):
                ws.cell(row=1, column=idx, value=col_name)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "accounts"
        ws.append(SHEET_HEADERS)

    target_row: int | None = None
    for row_idx in range(2, ws.max_row + 1):
        row_site = str(ws.cell(row=row_idx, column=1).value or "").strip().lower()
        row_user = str(ws.cell(row_idx, column=2).value or "").strip().lower()
        if row_site == norm_site and row_user == norm_user.lower():
            target_row = row_idx
            break

    values = [norm_site, norm_user, password, profile_url or "", ts, notes or ""]
    if target_row is not None:
        for col_idx, val in enumerate(values, start=1):
            # Keep existing non-empty password/profile if caller omitted it on update
            if val == "" and ws.cell(row=target_row, column=col_idx).value:
                continue
            ws.cell(row=target_row, column=col_idx, value=val)
    else:
        ws.append(values)

    wb.save(path)
    return path
