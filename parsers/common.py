"""Shared helpers for parser modules and HTML rendering."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple, Union

LabelSpec = Union[
    Tuple[str, str],
    Tuple[str, str, Callable[[Any, dict], Any]],
    Tuple[str, str, Callable[[Any, dict], Any], str],
]

DEFAULT_SLURM_LABELS: list[LabelSpec] = [
    ("Job ID", "job_id"),
    ("Job Name", "job_name"),
    ("User", "user"),
    ("Partition", "partition"),
    ("Nodes", "nodes"),
    ("Tasks (MPI)", "ntasks"),
    ("Requested Time", "time_limit"),
    ("Memory", "mem"),
    ("Launcher Tasks", "launcher_tasks"),
    ("Launch Command", "launch_command"),
    ("Submit Dir", "submit_dir"),
    ("Status", "status"),
    ("Actual Walltime", "actual_walltime"),
]

HARDWARE_LABELS: list[LabelSpec] = [
    ("Node", "hostname"),
    ("CPU Model", "cpu_model"),
    ("Architecture", "architecture"),
    ("Sockets", "sockets"),
    ("Cores / Socket", "cores_per_socket"),
    ("Threads / Core", "threads_per_core"),
    ("Total Cores", "total_cores"),
    ("Threads", "total_threads"),
    ("Memory (MB)", "mem_total_mb"),
]


IGNORE_TAG_STRINGS = {"false", "true", "none", "null", "unknown", "n/a"}


def read_text_file(path: str) -> tuple[Optional[str], Optional[str]]:
    try:
        with open(path, 'r', errors='replace') as handle:
            return handle.read(), None
    except Exception as exc:
        return None, str(exc)



def yes_no(value: Any, _: Optional[dict] = None) -> str:
    if value in (None, '', []):
        return '—'
    return 'Yes' if bool(value) else 'No'



def list_to_string(value: Any, _: Optional[dict] = None) -> str:
    if isinstance(value, (list, tuple, set)):
        return ', '.join(str(v) for v in value) if value else '—'
    return value if value not in (None, '') else '—'



def float_fmt(ndigits: int) -> Callable[[Any, dict], str]:
    def _fmt(value: Any, _: Optional[dict] = None) -> str:
        try:
            return f"{float(value):.{ndigits}f}"
        except Exception:
            return value if value not in (None, '') else '—'
    return _fmt



def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)



def html_value(value: Any) -> str:
    if value in (None, '', [], {}):
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, dict):
        if not value:
            return '—'
        items = ''.join(
            f"<tr><td>{html_escape(k)}</td><td>{html_escape(v)}</td></tr>"
            for k, v in value.items()
        )
        return f"<table border='1' cellpadding='3' cellspacing='0'>{items}</table>"
    if isinstance(value, (list, tuple, set)):
        return html_escape(', '.join(str(v) for v in value)) if value else '—'
    return html_escape(value)



def build_rows(source: dict, labels: Sequence[LabelSpec]) -> str:
    rows: list[str] = []
    for spec in labels:
        label, key = spec[0], spec[1]
        formatter = spec[2] if len(spec) >= 3 else None
        default = spec[3] if len(spec) >= 4 else '—'
        value = source.get(key)
        if formatter is not None:
            try:
                value = formatter(value, source)
            except Exception:
                value = source.get(key)
        if value in (None, '', [], {}):
            value = default
        rows.append(f"<tr><td><b>{html_escape(label)}</b></td><td>{html_value(value)}</td></tr>")
    return ''.join(rows)



def section_table(title: str, source: dict, labels: Sequence[LabelSpec]) -> str:
    if not labels:
        return ''
    return (
        f"<h2>{html_escape(title)}</h2>"
        "<table border='1' cellpadding='5' cellspacing='0'>"
        f"{build_rows(source, labels)}"
        "</table>"
    )



def section_table_if_any(title: str, source: dict, labels: Sequence[LabelSpec]) -> str:
    if not source:
        return ''
    meaningful = [key for _, key, *rest in labels if source.get(key) not in (None, '', [], {})]
    if not meaningful:
        return ''
    return section_table(title, source, labels)



def warnings_html(input_meta: dict, output_results: Optional[dict] = None) -> str:
    warnings: list[str] = []
    for src in (input_meta or {}, output_results or {}):
        for key in ('parse_errors', 'warnings'):
            value = src.get(key)
            if not value:
                continue
            if isinstance(value, str):
                warnings.append(value)
            else:
                warnings.extend(str(v) for v in value)
    warnings = [w for w in warnings if w]
    if not warnings:
        return ''
    return "<h2>Warnings</h2><ul>" + ''.join(f"<li>{html_escape(w)}</li>" for w in warnings) + "</ul>"



def format_standard_body(
    input_meta: dict,
    slurm_meta: dict,
    output_results: Optional[dict],
    *,
    job_labels: Sequence[LabelSpec],
    slurm_labels: Optional[Sequence[LabelSpec]] = None,
    result_labels: Optional[Sequence[LabelSpec]] = None,
    extra_sections: Optional[Iterable[str]] = None,
) -> str:
    parts = [section_table('Job Metadata', input_meta or {}, job_labels)]
    parts.append(section_table('Slurm Job Details', slurm_meta or {}, slurm_labels or DEFAULT_SLURM_LABELS))
    if output_results is not None and result_labels:
        parts.append(section_table('Results', output_results, result_labels))
    if extra_sections:
        parts.extend([s for s in extra_sections if s])
    parts.append(warnings_html(input_meta or {}, output_results))
    return ''.join(parts)



def collect_file_fields(input_meta: dict, field_names: Sequence[str]) -> list[str]:
    files: list[str] = []
    for field in field_names:
        value = input_meta.get(field)
        if not value:
            continue
        if isinstance(value, (list, tuple, set)):
            files.extend(str(v) for v in value if v)
        else:
            files.append(str(value))
    return files



def append_marked_section(body: str, marker: str, section_html: str) -> str:
    start = f"<!-- {marker}_START -->"
    end = f"<!-- {marker}_END -->"
    wrapped = f"{start}{section_html}{end}"
    if start in body and end in body:
        prefix = body.split(start, 1)[0]
        suffix = body.split(end, 1)[1]
        return prefix + wrapped + suffix
    return body + wrapped



def resolve_relative_file(submit_dir: str, value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str(Path(submit_dir) / value)



def clean_tag(value: Any) -> list[str]:
    if value in (None, '', [], {}):
        return []
    if isinstance(value, bool):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    tags: list[str] = []
    for item in values:
        if isinstance(item, bool):
            continue
        text = str(item).strip()
        if not text or text.lower() in IGNORE_TAG_STRINGS:
            continue
        text = re.sub(r'\s+', ' ', text)
        if len(text) > 64:
            text = text[:64]
        tags.append(text)
    return tags



def slugify_for_tag(value: Any, *, prefix: str = '') -> Optional[str]:
    if value in (None, '', [], {}, False, True):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('/', '_')
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'[^A-Za-z0-9_.:-]+', '_', text)
    text = text.strip('_.:-')
    if not text:
        return None
    if len(text) > 64:
        text = text[:64]
    return f"{prefix}{text}" if prefix else text
