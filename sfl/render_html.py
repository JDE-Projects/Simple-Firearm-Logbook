"""Export rendering: shared HTML/CSS builders used by both the single-firearm
export and the full-collection report. Deliberately a plain, light,
print-friendly look, independent of the app's own dark/light theme choice,
since these are meant to be read or printed outside the app."""
import datetime
import os
from decimal import Decimal

from sfl.utils import format_size

EXPORT_BASE_CSS = """
* { box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; background:#f5f5f2; color:#1a1a1a; margin:0; padding:24px; }
.report { max-width: 900px; margin: 0 auto; }
h1 { font-size: 22px; margin-bottom:4px; }
.report-meta { color:#555; font-size:12px; margin-bottom:24px; }
.firearm-card { background:#ffffff; border:1px solid #ddd; border-radius:8px; padding:20px 24px; margin-bottom:24px; }
.identity-block h2 { margin:0 0 2px; font-size:19px; }
.log-num { color:#666; font-size:12.5px; margin-bottom:12px; font-family: 'Courier New', monospace; }
table.id-table { border-collapse: collapse; width:100%; margin-bottom: 6px; }
table.id-table th { text-align:left; width:180px; padding:4px 10px 4px 0; color:#555; font-size:12.5px; font-weight:600; vertical-align:top; }
table.id-table td { padding:4px 0; font-size:13px; vertical-align:top; }
.disposition-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.disposition-block h3 { margin:0 0 8px; font-size:14px; color:#a33; }
.notes-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.notes-block h3 { margin:0 0 6px; font-size:14px; }
.notes-block p { font-size:13px; line-height:1.5; white-space:pre-wrap; margin:0; }
.photo-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.photo-block h3 { margin:0 0 10px; font-size:14px; }
.photo-grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; }
.photo-item { border:1px solid #ddd; border-radius:6px; overflow:hidden; background:#fafafa; text-align:center; font-size:11px; color:#777; padding:6px; }
.photo-item img { width:100%; height:auto; display:block; border-radius:4px; }
.photo-item.missing { padding:24px 8px; }
.photo-item.primary { border-color:#b8935a; }
.document-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.document-block h3 { margin:0 0 8px; font-size:14px; }
.document-list { margin:0; padding-left:18px; font-size:13px; line-height:1.7; }
.doc-size { color:#777; font-size:12px; }
"""

# Fresh page per firearm, keep-together blocks, capped 2-per-row photo grid,
# and a white background regardless of the viewer's own theme.
PRINT_CSS = """
@media print {
  body { background:#ffffff !important; color:#111 !important; }
  .firearm-card { page-break-before: always; border:none; box-shadow:none; }
  .firearm-card:first-child { page-break-before: avoid; }
  .identity-block, .notes-block, .disposition-block, .photo-block, .document-block { break-inside: avoid; page-break-inside: avoid; }
  .photo-grid { grid-template-columns: repeat(2, 1fr) !important; }
  .photo-grid img { max-width: 260px !important; max-height: 260px !important; }
}
"""


def _esc_html(s) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _fmt_price_html(s) -> str:
    if not s:
        return "Not set"
    try:
        value = Decimal(s)
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.2f}"
    except Exception:
        return _esc_html(s)


def _render_identity_block(f: dict) -> str:
    return f"""
    <div class="identity-block">
      <h2>{_esc_html(f['make'])} {_esc_html(f['model'])}</h2>
      <div class="log-num">Log #{_esc_html(f['log_number'])}</div>
      <table class="id-table">
        <tr><th>Type</th><td>{_esc_html(f['firearm_type']) or 'Not set'}</td></tr>
        <tr><th>Sub-Type</th><td>{_esc_html(f['sub_type']) or 'Not set'}</td></tr>
        <tr><th>Caliber</th><td>{_esc_html(f['caliber']) or 'Not set'}</td></tr>
        <tr><th>Serial Number</th><td>{_esc_html(f['serial_number']) or 'Not set'}</td></tr>
        <tr><th>Acquisition Date</th><td>{_esc_html(f['acquisition_date']) or 'Not set'}</td></tr>
        <tr><th>Acquired From</th><td>{_esc_html(f['acquired_from']) or 'Not set'}</td></tr>
        <tr><th>Purchase Price</th><td>{_fmt_price_html(f['purchase_price'])}</td></tr>
        <tr><th>Estimated Value</th><td>{_fmt_price_html(f['estimated_value'])}</td></tr>
        <tr><th>Insured Value</th><td>{_fmt_price_html(f['insured_value'])}</td></tr>
        <tr><th>Storage Location</th><td>{_esc_html(f['storage_location']) or 'Not set'}</td></tr>
        {_render_trust_nfa_rows(f)}
      </table>
    </div>
    """


def _render_trust_nfa_rows(f: dict) -> str:
    """Trust and NFA rows are left out entirely for an ordinary firearm, so
    the identity table stays clean when neither applies."""
    rows = []
    if f["held_in_trust"]:
        rows.append("<tr><th>Held In Trust</th><td>Yes</td></tr>")
        rows.append(f"<tr><th>Trust Name</th><td>{_esc_html(f['trust_name']) or 'Not set'}</td></tr>")
    if f["is_nfa"]:
        rows.append("<tr><th>NFA</th><td>Yes</td></tr>")
        rows.append(f"<tr><th>NFA Form Type</th><td>{_esc_html(f['nfa_form_type']) or 'Not set'}</td></tr>")
        rows.append(f"<tr><th>NFA Stamp Date</th><td>{_esc_html(f['nfa_stamp_date']) or 'Not set'}</td></tr>")
    return "".join(rows)


def _render_disposition_block(f: dict) -> str:
    if f["disposition_status"] == "Owned":
        return ""
    return f"""
    <div class="disposition-block">
      <h3>Disposition: {_esc_html(f['disposition_status'])}</h3>
      <table class="id-table">
        <tr><th>Date</th><td>{_esc_html(f['disposition_date']) or 'Not set'}</td></tr>
        <tr><th>To</th><td>{_esc_html(f['disposition_to']) or 'Not set'}</td></tr>
        <tr><th>Address</th><td>{_esc_html(f['disposition_address']) or 'Not set'}</td></tr>
        <tr><th>Amount</th><td>{_fmt_price_html(f['disposition_amount'])}</td></tr>
        <tr><th>Notes</th><td>{_esc_html(f['disposition_notes']) or 'Not set'}</td></tr>
      </table>
    </div>
    """


def _render_notes_block(f: dict) -> str:
    if not f["notes"]:
        return ""
    return (
        '<div class="notes-block"><h3>Notes</h3><p>'
        + _esc_html(f["notes"]).replace("\n", "<br>")
        + "</p></div>"
    )


def _render_photo_block_embedded(photos_with_data: list) -> str:
    if not photos_with_data:
        return ""
    items = []
    for p in photos_with_data:
        cls = "photo-item primary" if p.get("is_primary") else "photo-item"
        if p.get("data_uri"):
            items.append(f'<div class="{cls}"><img src="{p["data_uri"]}" alt="Photo"></div>')
        else:
            name = _esc_html(os.path.basename(p["filename"]))
            items.append(f'<div class="{cls} missing">{name}<br><span>Photo file missing</span></div>')
    return f'<div class="photo-block"><h3>Photos</h3><div class="photo-grid">{"".join(items)}</div></div>'


def _render_photo_block_relative(photos: list) -> str:
    """Photo block for the full report, which references the relative
    photos\\ paths shipped alongside it in the export zip rather than
    embedding data. If the report is opened without extracting the zip
    first, each slot falls back to the filename and a note."""
    if not photos:
        return ""
    items = []
    for p in photos:
        rel = _esc_html(p["filename"].replace("\\", "/"))
        name = _esc_html(os.path.basename(p["filename"]))
        cls = "photo-item primary" if p.get("is_primary") else "photo-item"
        items.append(
            f'<div class="{cls}"><img src="{rel}" alt="Photo" '
            f"onerror=\"this.parentNode.className='{cls} missing';"
            f"this.parentNode.innerHTML='{name}&lt;br&gt;&lt;span&gt;Extract the zip first to see photos.&lt;/span&gt;';\">"
            f"</div>"
        )
    return f'<div class="photo-block"><h3>Photos</h3><div class="photo-grid">{"".join(items)}</div></div>'


def _render_document_block(attachments: list) -> str:
    """Documents are never embedded (arbitrary file types), so both the
    single export and the full report just list each label and size."""
    if not attachments:
        return ""
    items = "".join(
        f'<li>{_esc_html(a["label"])} <span class="doc-size">({format_size(a["size_bytes"])})</span></li>'
        for a in attachments
    )
    return f'<div class="document-block"><h3>Documents</h3><ul class="document-list">{items}</ul></div>'


def _build_single_export_html(f: dict, photos_with_data: list, attachments: list) -> str:
    title = f"{f['log_number']} - {f['make']} {f['model']}"
    body = (
        _render_identity_block(f)
        + _render_disposition_block(f)
        + _render_notes_block(f)
        + _render_photo_block_embedded(photos_with_data)
        + _render_document_block(attachments)
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc_html(title)}</title>
<style>{EXPORT_BASE_CSS}{PRINT_CSS}</style>
</head><body>
<div class="report">
<div class="firearm-card">{body}</div>
</div>
</body></html>"""


def _build_full_report_html(firearms: list, photos_by_firearm: dict, attachments_by_firearm: dict) -> str:
    cards = []
    for f in firearms:
        photos = photos_by_firearm.get(f["id"], [])
        attachments = attachments_by_firearm.get(f["id"], [])
        body = (
            _render_identity_block(f)
            + _render_disposition_block(f)
            + _render_notes_block(f)
            + _render_photo_block_relative(photos)
            + _render_document_block(attachments)
        )
        cards.append(f'<div class="firearm-card">{body}</div>')
    title = "Firearm Logbook Export"
    generated = datetime.date.today().isoformat()
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc_html(title)}</title>
<style>{EXPORT_BASE_CSS}{PRINT_CSS}</style>
</head><body>
<div class="report">
<h1>{_esc_html(title)}</h1>
<div class="report-meta">Generated {generated}, {len(firearms)} firearm(s)</div>
{"".join(cards)}
</div>
</body></html>"""
