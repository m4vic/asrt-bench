"""The version store and the diff engine -- the regression half of the tool."""

from __future__ import annotations

import pytest

from asrtbench import store
from asrtbench.diff import compare, IncomparableRuns
from asrtbench.runner import RunResult, CaseOutcome


def _run(pack_hash: str, verdicts: dict[str, str], target: str = "t") -> RunResult:
    outcomes = [CaseOutcome(attack_id=aid, category="c", verdict=v, reason="", evidence_seq=[])
                for aid, v in verdicts.items()]
    return RunResult(run_id="run-x", target=target, pack_hash=pack_hash, outcomes=outcomes)


# ---------- store round-trips ----------

def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ASRT_BENCH_STORE", str(tmp_path))
    r = _run("hash1", {"a:1": "success", "a:2": "failure"})
    store.save(r, "v1")
    assert store.exists("v1")
    loaded = store.load("v1")
    assert loaded.pack_hash == "hash1"
    assert {o.attack_id: o.verdict for o in loaded.outcomes} == {"a:1": "success", "a:2": "failure"}


def test_list_versions_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("ASRT_BENCH_STORE", str(tmp_path))
    store.save(_run("h", {"a:1": "failure"}), "old")
    store.save(_run("h", {"a:1": "failure"}), "new")
    versions = [v["version"] for v in store.list_versions()]
    assert set(versions) == {"old", "new"}


# ---------- diff correctness ----------

def test_newly_broken_is_detected():
    base = _run("h", {"a:1": "failure", "a:2": "failure"})
    cand = _run("h", {"a:1": "success", "a:2": "failure"})  # a:1 became exploitable
    report = compare(base, cand)
    assert [c.attack_id for c in report.newly_broken] == ["a:1"]
    assert report.verdict() == "regressed"


def test_newly_fixed_is_detected():
    base = _run("h", {"a:1": "success"})
    cand = _run("h", {"a:1": "failure"})
    report = compare(base, cand)
    assert [c.attack_id for c in report.newly_fixed] == ["a:1"]
    assert report.verdict() == "improved"


def test_unchanged_is_unchanged():
    base = _run("h", {"a:1": "success", "a:2": "failure"})
    cand = _run("h", {"a:1": "success", "a:2": "failure"})
    report = compare(base, cand)
    assert report.verdict() == "unchanged"
    assert len(report.stable_broken) == 1
    assert len(report.stable_safe) == 1


def test_unclear_transitions_are_inconclusive_not_broken():
    base = _run("h", {"a:1": "failure"})
    cand = _run("h", {"a:1": "unclear"})
    report = compare(base, cand)
    assert not report.newly_broken
    assert not report.newly_fixed
    assert [c.attack_id for c in report.inconclusive] == ["a:1"]


# ---------- the two gates that keep a diff honest ----------

def test_mismatched_pack_hash_is_refused():
    base = _run("hashA", {"a:1": "failure"})
    cand = _run("hashB", {"a:1": "success"})
    with pytest.raises(IncomparableRuns, match="different attack packs"):
        compare(base, cand)


def test_zero_overlap_is_an_error_not_no_change():
    base = _run("h", {"a:1": "failure"})
    cand = _run("h", {"b:9": "failure"})  # no shared attacks
    with pytest.raises(IncomparableRuns, match="share no attacks"):
        compare(base, cand)


def test_partial_overlap_reports_the_excluded_ids():
    base = _run("h", {"a:1": "failure", "a:2": "failure"})
    cand = _run("h", {"a:1": "success", "a:3": "failure"})
    report = compare(base, cand)
    assert report.only_in_baseline == ["a:2"]
    assert report.only_in_candidate == ["a:3"]
    assert [c.attack_id for c in report.newly_broken] == ["a:1"]
