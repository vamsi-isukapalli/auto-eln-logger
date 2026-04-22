"""Parser registry and dispatch helpers."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

from .common import clean_tag, collect_file_fields, slugify_for_tag
from .cp2k_parser import (
    parse_cp2k_input, parse_cp2k_output, format_elabftw_body as format_cp2k_body,
    TAG_FIELDS as CP2K_TAG_FIELDS, FILE_FIELDS as CP2K_FILE_FIELDS,
)
from .molpro_parser import (
    parse_molpro_input, parse_molpro_output, format_elabftw_body_molpro,
    TAG_FIELDS as MOLPRO_TAG_FIELDS, FILE_FIELDS as MOLPRO_FILE_FIELDS,
)
from .orca_parser import (
    parse_orca_input, parse_orca_output, format_elabftw_body_orca,
    TAG_FIELDS as ORCA_TAG_FIELDS, FILE_FIELDS as ORCA_FILE_FIELDS,
)
from .amber_parser import (
    parse_amber_input, parse_amber_output, parse_amber_submit_steps, format_elabftw_body_amber,
    TAG_FIELDS as AMBER_TAG_FIELDS, FILE_FIELDS as AMBER_FILE_FIELDS,
)
from .openmolcas_parser import (
    parse_openmolcas_input, parse_openmolcas_output, format_elabftw_body_openmolcas,
    TAG_FIELDS as OPENMOLCAS_TAG_FIELDS, FILE_FIELDS as OPENMOLCAS_FILE_FIELDS,
)

PARSER_REGISTRY = {
    'CP2K': {
        'parse_input': parse_cp2k_input,
        'parse_output': parse_cp2k_output,
        'format_body': format_cp2k_body,
        'tag_fields': CP2K_TAG_FIELDS,
        'file_fields': CP2K_FILE_FIELDS,
        'fallback_outputs': ['.log', '.out'],
        'script_output_patterns': [r'>\s*(\S+\.log)', r'>\s*(\S+\.out)'],
    },
    'Molpro': {
        'parse_input': parse_molpro_input,
        'parse_output': parse_molpro_output,
        'format_body': format_elabftw_body_molpro,
        'tag_fields': MOLPRO_TAG_FIELDS,
        'file_fields': MOLPRO_FILE_FIELDS,
        'fallback_outputs': ['.out', '.log'],
        'script_output_patterns': [r'(?:>|1>)\s*(\S+\.out)', r'>\s*(\S+\.log)'],
    },
    'ORCA': {
        'parse_input': parse_orca_input,
        'parse_output': parse_orca_output,
        'format_body': format_elabftw_body_orca,
        'tag_fields': ORCA_TAG_FIELDS,
        'file_fields': ORCA_FILE_FIELDS,
        'fallback_outputs': ['.log', '.out'],
        'script_output_patterns': [r'>\s*(\S+\.log)', r'>\s*(\S+\.out)'],
    },
    'Amber': {
        'parse_input': parse_amber_input,
        'parse_output': parse_amber_output,
        'parse_script_steps': parse_amber_submit_steps,
        'format_body': format_elabftw_body_amber,
        'tag_fields': AMBER_TAG_FIELDS,
        'file_fields': AMBER_FILE_FIELDS,
        'fallback_outputs': ['.out', '.log'],
        'script_output_patterns': [r'\s-o\s+(\S+)', r'>\s*(\S+\.out)'],
    },
    'OpenMolcas': {
        'parse_input': parse_openmolcas_input,
        'parse_output': parse_openmolcas_output,
        'format_body': format_elabftw_body_openmolcas,
        'tag_fields': OPENMOLCAS_TAG_FIELDS,
        'file_fields': OPENMOLCAS_FILE_FIELDS,
        'fallback_outputs': ['.out', '.log'],
        'script_output_patterns': [r'(?:>&|>)\s*(\S+\.out)', r'>\s*(\S+\.log)'],
    },
}


def get_parser(code: str) -> Optional[dict]:
    return PARSER_REGISTRY.get(code)


def parse_input_for_code(code: str, input_path: str) -> dict:
    if not input_path:
        return {'code': code, 'input_file': '', 'parse_errors': []}
    parser = get_parser(code)
    if not parser:
        return {
            'code': code,
            'input_file': Path(input_path).name,
            'parse_errors': [f'No input parser registered for {code}'],
        }
    return parser['parse_input'](input_path)


def parse_output_for_code(code: str, output_path: str):
    parser = get_parser(code)
    if not parser:
        return None
    return parser['parse_output'](output_path)


def parse_script_steps_for_code(code: str, script_content: str):
    parser = get_parser(code)
    if not parser or 'parse_script_steps' not in parser:
        return []
    return parser['parse_script_steps'](script_content)


def workflow_html(input_meta: dict) -> str:
    wf_type = input_meta.get('workflow_type')
    if not wf_type or wf_type == 'single':
        return ''
    rows = [
        f"<tr><td><b>Workflow Type</b></td><td>{html.escape(str(wf_type))}</td></tr>",
        f"<tr><td><b>Description</b></td><td>{html.escape(str(input_meta.get('workflow_description') or '—'))}</td></tr>",
    ]
    if input_meta.get('workflow_step_source'):
        rows.append(f"<tr><td><b>Step Source</b></td><td>{html.escape(str(input_meta['workflow_step_source']))}</td></tr>")
    if input_meta.get('workflow_step_count') is not None:
        rows.append(f"<tr><td><b>Step Count</b></td><td>{html.escape(str(input_meta['workflow_step_count']))}</td></tr>")
    if input_meta.get('workflow_array_spec'):
        rows.append(f"<tr><td><b>Array Spec</b></td><td>{html.escape(str(input_meta['workflow_array_spec']))}</td></tr>")
    if input_meta.get('workflow_primary_inputs'):
        rows.append(f"<tr><td><b>Primary Inputs</b></td><td>{html.escape(', '.join(input_meta['workflow_primary_inputs']))}</td></tr>")
    html_body = '<h2>Workflow Details</h2><table border="1" cellpadding="5" cellspacing="0">' + ''.join(rows) + '</table>'
    steps = input_meta.get('workflow_steps') or []
    if steps:
        step_rows = ['<tr><th>#</th><th>Name</th><th>Status</th></tr>']
        for step in steps[:100]:
            step_rows.append(
                f"<tr><td>{step.get('step_index', 0) + 1}</td><td>{html.escape(str(step.get('display_name') or step.get('step_id') or ''))}</td><td>{html.escape(str(step.get('status') or 'PENDING'))}</td></tr>"
            )
        if len(steps) > 100:
            step_rows.append(f"<tr><td colspan='3'><i>Showing first 100 of {len(steps)} steps.</i></td></tr>")
        html_body += '<h3>Workflow Steps</h3><table border="1" cellpadding="5" cellspacing="0">' + ''.join(step_rows) + '</table>'
    return html_body


def format_body_for_code(code: str, input_meta: dict, slurm_meta: dict, output_results=None) -> str:
    parser = get_parser(code)
    if not parser:
        body = (
            f"<h2>Job Metadata</h2><p><b>Code:</b> {html.escape(str(code))}<br>"
            f"<b>Input File:</b> {html.escape(str(input_meta.get('input_file', '—') or '—'))}</p>"
            f"<h2>Slurm Job Details</h2><p><b>Status:</b> {html.escape(str(slurm_meta.get('status', '—')))}</p>"
        )
        if output_results:
            body += f"<h2>Results</h2><p>{html.escape(str(output_results))}</p>"
    else:
        body = parser['format_body'](input_meta, slurm_meta, output_results)
    extra = workflow_html(input_meta)
    if extra:
        body += extra
    return body


def build_tags_for_code(code: str, input_meta: dict, slurm_meta: dict) -> list[str]:
    parser = get_parser(code)
    tags: list[str] = []
    seen: set[str] = set()

    def add_tag(tag: str):
        if not tag:
            return
        key = tag.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(tag)

    for value in [code, slurm_meta.get('user'), slurm_meta.get('partition')]:
        for tag in clean_tag(value):
            add_tag(tag)

    project_tag = slugify_for_tag(input_meta.get('project_label'), prefix='project:')
    if project_tag:
        add_tag(project_tag)

    wf_type = input_meta.get('workflow_type')
    if wf_type and wf_type != 'single':
        add_tag(f'workflow:{wf_type}')

    if parser:
        for field in parser.get('tag_fields', []):
            for tag in clean_tag(input_meta.get(field)):
                add_tag(tag)

    if input_meta.get('qmmm'):
        add_tag('QM/MM')
    if input_meta.get('amber_steps'):
        add_tag('multi-step')
    return tags


def collect_referenced_files(code: str, input_meta: dict) -> list[str]:
    parser = get_parser(code)
    files = []
    if parser:
        files.extend(collect_file_fields(input_meta, parser.get('file_fields', [])))
    for key in ('workflow_step_source',):
        val = input_meta.get(key)
        if val:
            files.append(val)
    for val in input_meta.get('workflow_primary_inputs', []) or []:
        if val:
            files.append(val)
    return files


def guess_output_file(code: str, submit_dir: str, input_file: str, sbatch_directives: dict, script_content: str, workflow: Optional[dict] = None) -> str:
    if workflow and workflow.get('main_output'):
        return workflow['main_output']
    parser = get_parser(code) or {}
    candidates: list[str] = []
    sbatch_output = sbatch_directives.get('output', '').strip()
    if sbatch_output:
        candidates.append(sbatch_output)
    for pattern in parser.get('script_output_patterns', []):
        for match in re.findall(pattern, script_content):
            candidates.append(match)
    base = Path(input_file).stem if input_file else Path(Path(script_content.splitlines()[0] if script_content.splitlines() else 'job').name).stem
    for suffix in parser.get('fallback_outputs', ['.log', '.out']):
        candidates.append(base + suffix)

    cleaned: list[str] = []
    for item in candidates:
        item = item.strip().strip('"\'')
        if not item or '$' in item:
            continue
        path = Path(item)
        if not path.is_absolute():
            path = Path(submit_dir) / path
        cleaned.append(str(path))
    for item in cleaned:
        if Path(item).exists():
            return item
    return cleaned[0] if cleaned else str(Path(submit_dir) / f"{base}.out")
