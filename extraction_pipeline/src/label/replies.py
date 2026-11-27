"""Step 7 replies: id and context checks, unit quarantine, and the Labeller (retries)."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from ..llm.client import Client, canonical, digest, parse_reply, save_json
from ..validate import check_reply
from .requests import request_key, response_schema


def context_flags(row: dict, order: dict[str, int]) -> list[str]:
    refs, flags = row["context_ids"], []
    if len(refs) != len(set(refs)):
        flags.append("duplicate_context_references")
    if any(c not in order for c in refs):
        flags.append("context_not_supplied")
    if any(c in order and order[c] >= order[row["id"]] for c in refs):
        flags.append("self_or_future_context_reference")
    if all(c in order for c in refs) and [order[c] for c in refs] != sorted(order[c] for c in refs):
        flags.append("context_not_in_source_order")
    return flags


def quarantine(batch: dict, results: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Claudia's project_targets(): one row per expected id; units with problems become needs_review.

    Returns (rows, held units, rows with ids not in the batch). No id is reassigned.
    """
    groups = defaultdict(list)
    for row in results:
        groups[row["id"]].append(row)
    expected = batch["local_ids"]
    order = {s: i for i, s in enumerate(batch["context_local_ids"] + expected)}
    rows, held = [], []
    for sid in expected:
        originals = groups[sid]
        flags = (["missing_model_target_id"] if not originals else
                 ["duplicate_model_target_id"] if len(originals) > 1 else context_flags(originals[0], order))
        if not flags:
            rows.append(deepcopy(originals[0]))
            continue
        if len(originals) == 1:
            row = deepcopy(originals[0])
            row["label"], row["context_ids"] = "needs_review", []
        else:
            row = {"id": sid, "label": "needs_review", "context_ids": [], "subject_scope": [],
                   "annotation_note": None, "is_boilerplate": None, "time_reference": None}
        row["annotation_note"] = ("CODE REVIEW ONLY: " + ", ".join(flags) + ". No candidate Claim published; "
                                  "original raw rows retained in unit_quarantine.json. "
                                  "This is not a model classification.")
        held.append({"unit_id": batch["unit_ids"][expected.index(sid)], "local_id": sid,
                     "review_flags": flags, "original_model_assessments": deepcopy(originals),
                     "processing_projection": deepcopy(row), "disposition": "held_for_review_no_candidate"})
        rows.append(row)
    extras = [deepcopy(r) for r in results if r["id"] not in set(expected)]
    return rows, held, extras



class Labeller(Client):
    """The client, plus one batch's reply checked, quarantined and retried (max_retries)."""

    def label(self, plan: dict, send: bool) -> dict:
        """Reply for one batch: {record, problems, results, held, sent}; follows retries if set.

        results has one row per expected id once the reply is accepted; held lists the
        units set aside by quarantine().
        """
        attempt_plan, sent = plan, False
        out = {"record": None, "problems": [], "results": [], "held": [], "sent": False}
        for attempt in range(self.cfg["max_retries"] + 1):
            record = self.cached(attempt_plan)
            if record is None:
                if not send:
                    return dict(out, sent=sent)
                record = self.send(attempt_plan)
                sent = True
            folder = self.folder(attempt_plan["request_hash"])
            problems, parsed = parse_reply(record["raw_response"], response_schema()["schema"], "results")
            if not problems:
                results, held, extras = parsed["results"], [], []
                if check_reply(plan["batch"], results):
                    results, held, extras = quarantine(plan["batch"], results)
                    problems = check_reply(plan["batch"], results)      # must now pass
                if not problems:
                    (folder / "validation_errors.json").unlink(missing_ok=True)
                    if held or extras:
                        save_json(folder / "unit_quarantine.json", {
                            "request_hash": attempt_plan["request_hash"], "batch_id": plan["batch"]["batch_id"],
                            "quarantined_units": held, "unexpected_model_rows": extras})
                    return {"record": record, "problems": [], "results": results, "held": held, "sent": sent}
            save_json(folder / "validation_errors.json", problems)
            if attempt == self.cfg["max_retries"]:
                break
            content = record["raw_response"]["choices"][0]["message"].get("content") or "" \
                if record["raw_response"].get("choices") else ""
            request = deepcopy(attempt_plan["request"])
            request["messages"] += [{"role": "assistant", "content": content},
                                    {"role": "user", "content": "Your reply had these problems: "
                                     + "; ".join(problems) + ". Return the complete corrected result for every sentence."}]
            identity = {**attempt_plan["identity"], "request": request, "retry_of": attempt_plan["request_hash"]}
            attempt_plan = {**plan, "request": request, "identity": identity,
                            "request_hash": digest(canonical(request_key(request,
                                                                         retry_of=attempt_plan["request_hash"])))}
        return {"record": record, "problems": problems, "results": [], "held": [], "sent": sent}
