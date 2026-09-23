"""Orchestrate one run: config in, canonical JSON out (``run.json`` plus one file per EIP)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from eip_complexity import RESULT_SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION
from eip_complexity.cache import EvaluationCache, cache_key, write_json_atomic
from eip_complexity.config import NOT_APPLICABLE_LAYERS, RunConfig, load_config
from eip_complexity.eips import EipDocument, EipsRepo, extract_candidates
from eip_complexity.errors import ComplexityError
from eip_complexity.gitinfo import describe_repo, public_git_info
from eip_complexity.hashing import sha256_bytes
from eip_complexity.jev import JevClient, TypeSafeJevClient, is_concrete_model, sdk_version
from eip_complexity.questions import (
    QUESTION_SET_VERSION,
    build_questions,
    build_request_payload,
    question_set_sha256,
    request_sha256,
)
from eip_complexity.render import render_run
from eip_complexity.scoring import score_response
from eip_complexity.template import AssessmentTemplate, TemplateSource, load_template

log = logging.getLogger(TOOL_NAME)

METHODOLOGY = {
    "summary": (
        "Each EIP is judged by one Jev (TypeSafe System One) request containing one Choice question per "
        "checklist criterion plus one yes/no Choice per candidate interacting EIP. Criterion scores are the "
        "chosen option; the Cross-EIP bonus, total and tier are computed in code from the template's rules."
    ),
    "limitations": [
        "Only the target EIP's own Markdown is supplied to Jev. Dependency EIPs, implementation status, devnet "
        "history and discussion threads are not available to the model, and the questions instruct it not to "
        "assume them.",
        "Cross-EIP candidates are derived solely from the target EIP's frontmatter `requires` field and explicit "
        "EIP-NNNN references in its text.",
        "Jev does not produce prose. The template's Rationale, Special Considerations and Notes sections are "
        "therefore absent (null), not generated.",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evaluator_provenance(question_sha: str, exclude_from_dirty: tuple[Path, ...]) -> dict:
    """Captured once per run, before any output is written, so the run's own files never count as dirt."""
    return {
        "name": TOOL_NAME,
        "version": TOOL_VERSION,
        "git": public_git_info(describe_repo(Path(__file__).resolve(), exclude=exclude_from_dirty)),
        "question_set_version": QUESTION_SET_VERSION,
        "question_set_sha256": question_sha,
    }


def _template_provenance(template: AssessmentTemplate, source: TemplateSource) -> dict:
    return {"revision": template.revision, "anchor_count": len(template.anchors), **source.to_dict()}


def evaluate_document(
    document: EipDocument,
    template: AssessmentTemplate,
    template_sha256: str,
    question_sha: str,
    model_to_send: str,
    client: JevClient,
) -> dict:
    """One Jev request for one EIP, returned as the semantic ``evaluation`` block."""
    candidates = extract_candidates(document)
    questions = build_questions(template, candidates)
    payload = build_request_payload(document.markdown, model_to_send, questions)
    where = f"EIP-{document.number}"
    log.info("%s: one Jev request (%s) with %d questions (%d anchors, %d cross-EIP candidates)",
             where, model_to_send, len(questions), len(template.anchors), len(candidates))
    response = client.evaluate(payload)
    scores = score_response(template, questions, candidates, response.raw, where=where)
    return {
        "eip": {
            "number": document.number,
            "title": document.title,
            "source_path": document.path,
            "content_sha256": document.sha256,
            "requires": list(document.requires),
        },
        "template": {"revision": template.revision, "anchor_count": len(template.anchors), "sha256": template_sha256},
        "question_set": {"version": QUESTION_SET_VERSION, "sha256": question_sha},
        "jev": {
            "model_sent": model_to_send,
            "resolved_model": response.model,
            "sdk_version": sdk_version(),
            "request_id": response.request_id,
            "usage": response.usage,
        },
        "request": {"sha256": request_sha256(payload), "payload": payload},
        "response": {"raw": response.raw},
        "scores": scores,
        "rationale": None,
        "special_considerations": None,
        "notes": None,
        "evaluated_at": _now(),
    }


def _check_cached(evaluation: dict, document: EipDocument, template_sha256: str, question_sha: str, model: str, key: str) -> None:
    where = f"cache entry {key[:12]} for EIP-{document.number}"
    expectations = {
        ("eip", "number"): document.number,
        ("eip", "content_sha256"): document.sha256,
        ("template", "sha256"): template_sha256,
        ("question_set", "sha256"): question_sha,
        ("jev", "resolved_model"): model,
    }
    for (section, field), expected in expectations.items():
        actual = evaluation.get(section, {}).get(field) if isinstance(evaluation.get(section), dict) else None
        if actual != expected:
            raise ComplexityError(f"{where}: {section}.{field} is {actual!r}, expected {expected!r}")
    if not isinstance(evaluation.get("scores"), dict) or "total_score" not in evaluation["scores"]:
        raise ComplexityError(f"{where}: no scores")


def run(config_path: Path, *, client_factory: Callable[[], JevClient] = TypeSafeJevClient) -> Path:
    """Execute a run from its config and return the path of the written ``run.json``."""
    config = load_config(config_path)
    run_owned = (config.output_dir, config.cache_dir)
    template, template_source = load_template(config.complexity_template, exclude_from_dirty=run_owned)
    question_sha = question_set_sha256(template)
    evaluator = _evaluator_provenance(question_sha, run_owned)
    template_provenance = _template_provenance(template, template_source)
    log.info("template %s: revision %d, %d anchors, sha256 %s", config.complexity_template, template.revision,
             len(template.anchors), template_source.sha256[:12])

    repo = EipsRepo(config.eips_repo)
    origin_url = repo.origin_url()
    resolved = repo.resolve(config.eips_ref)
    log.info("EIPs repo %s (%s): %s -> %s", repo.path, origin_url, resolved.remote_ref or config.eips_ref, resolved.commit)
    documents = [repo.read_eip(resolved.commit, entry.number) for entry in config.eips]

    cache = EvaluationCache(config.cache_dir)
    evaluations_dir = config.output_dir / "evaluations"
    resolved_model = config.jev_model if is_concrete_model(config.jev_model) else None
    if resolved_model is None:
        log.info("jev_model %r is an alias; the first EIP's response pins the concrete model for this run",
                 config.jev_model)
    client: JevClient | None = None
    manifest_entries: list[dict] = []
    eips_repo_info = {"path": str(repo.path.resolve()), "origin_url": origin_url, **resolved.to_dict()}
    try:
        for entry, document in zip(config.eips, documents, strict=True):
            if not entry.applicable:
                reason = (f"layer {entry.layer!r} is outside the execution-layer checklist "
                          f"(not applicable layers: {list(NOT_APPLICABLE_LAYERS)})")
                result = {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "kind": "eip-complexity-not-applicable",
                    "eip_number": document.number,
                    "layer": entry.layer,
                    "reason": reason,
                    "eip": {"number": document.number, "title": document.title, "source_path": document.path,
                            "git_blob_sha": document.blob_sha, "content_sha256": document.sha256},
                    "provenance": {
                        "eips_repo": eips_repo_info,
                        "template": template_provenance,
                        "evaluator": evaluator,
                        "jev": {"requested_model": config.jev_model, "resolved_model": resolved_model, "sdk_version": sdk_version()},
                    },
                }
                result_path = evaluations_dir / f"eip-{document.number}.not-applicable.json"
                write_json_atomic(result_path, result)
                log.info("EIP-%d %s: not applicable (%s)", document.number, document.title, entry.layer)
                manifest_entries.append({
                    "number": document.number, "stage": entry.stage, "layer": entry.layer,
                    "status": "not_applicable", "title": document.title,
                    "path": result_path.relative_to(config.output_dir).as_posix(),
                    "sha256": sha256_bytes(result_path.read_bytes()), "reason": reason,
                })
                continue
            cached_hit = False
            key = None
            evaluation = None
            if resolved_model is not None and not config.force:
                key = cache_key(eip_sha256=document.sha256, template_sha256=template_source.sha256,
                                question_set_sha256=question_sha, model=resolved_model)
                evaluation = cache.load(key)
                if evaluation is not None:
                    _check_cached(evaluation, document, template_source.sha256, question_sha, resolved_model, key)
                    cached_hit = True
                    log.info("EIP-%d: reusing cached evaluation %s", document.number, key[:12])
            if evaluation is None:
                if client is None:
                    client = client_factory()
                evaluation = evaluate_document(document, template, template_source.sha256, question_sha,
                                               resolved_model or config.jev_model, client)
                model = evaluation["jev"]["resolved_model"]
                if resolved_model is None:
                    resolved_model = model
                    log.info("pinned Jev model for this run: %s", resolved_model)
                elif model != resolved_model:
                    raise ComplexityError(
                        f"EIP-{document.number}: Jev answered with model {model!r} but this run is pinned to {resolved_model!r}"
                    )
                key = cache_key(eip_sha256=document.sha256, template_sha256=template_source.sha256,
                                question_set_sha256=question_sha, model=resolved_model)
                cache.store(key, evaluation)

            result = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "kind": "eip-complexity-evaluation",
                "eip_number": document.number,
                "provenance": {
                    "eips_repo": eips_repo_info,
                    "eip_source": {"path": document.path, "git_blob_sha": document.blob_sha, "content_sha256": document.sha256},
                    "template": template_provenance,
                    "evaluator": evaluator,
                    "jev": {"requested_model": config.jev_model, "resolved_model": resolved_model, "sdk_version": sdk_version()},
                },
                "cache": {"key": key, "hit": cached_hit},
                "evaluation": evaluation,
            }
            result_path = evaluations_dir / f"eip-{document.number}.json"
            write_json_atomic(result_path, result)
            scores = evaluation["scores"]
            log.info("EIP-%d %s: total %d (%s %s) -> %s", document.number, document.title, scores["total_score"],
                     scores["tier"]["emoji"], scores["tier"]["name"], result_path)
            manifest_entries.append(
                {
                    "number": document.number,
                    "stage": entry.stage,
                    "layer": entry.layer,
                    "status": "evaluated",
                    "title": document.title,
                    "path": result_path.relative_to(config.output_dir).as_posix(),
                    "sha256": sha256_bytes(result_path.read_bytes()),
                    "total_score": scores["total_score"],
                    "tier": scores["tier"],
                    "cache_hit": cached_hit,
                }
            )
    finally:
        if client is not None:
            client.close()

    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "kind": "eip-complexity-run",
        "created_at": _now(),
        "fork": config.fork,
        "meta_eip": config.meta_eip,
        "human_assessments": config.human_assessments.to_dict() if config.human_assessments else None,
        "comparison": config.comparison.to_dict() if config.comparison else None,
        "config": {
            "path": str(config.path),
            "sha256": config.sha256,
            "force": config.force,
            "render_html": config.render_html,
            "eips": [{"number": e.number, "stage": e.stage, "layer": e.layer} for e in config.eips],
        },
        "not_applicable_layers": list(NOT_APPLICABLE_LAYERS),
        "eips_repo": eips_repo_info,
        "template": template_provenance,
        "evaluator": evaluator,
        "jev": {"requested_model": config.jev_model, "resolved_model": resolved_model, "sdk_version": sdk_version()},
        "evaluations": manifest_entries,
        "methodology": METHODOLOGY,
    }
    run_path = config.output_dir / "run.json"
    write_json_atomic(run_path, manifest)
    log.info("wrote %s", run_path)
    if config.render_html:
        html_path = render_run(run_path)
        log.info("wrote %s", html_path)
    return run_path
