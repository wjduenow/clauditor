"""Tests for clauditor.workspace — iteration workspace allocator."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from clauditor.workspace import (
    InvalidSkillNameError,
    IterationExistsError,
    IterationWorkspace,
    allocate_iteration,
    validate_skill_name,
)


class TestAllocateIteration:
    def test_allocate_empty_clauditor_dir_returns_iteration_one(
        self, tmp_path: Path
    ) -> None:
        ws = allocate_iteration(tmp_path, "foo")
        assert ws.iteration == 1
        assert ws.final_path == tmp_path / "iteration-1" / "foo"
        assert ws.tmp_path.is_dir()
        assert ws.tmp_path == tmp_path / "iteration-1-tmp" / "foo"

    def test_allocate_with_gaps_skips_to_max_plus_one(self, tmp_path: Path) -> None:
        (tmp_path / "iteration-1").mkdir()
        (tmp_path / "iteration-3").mkdir()
        ws = allocate_iteration(tmp_path, "foo")
        assert ws.iteration == 4

    def test_allocate_ignores_non_iteration_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "iteration-foo").mkdir()
        (tmp_path / "iteration-2-tmp").mkdir()
        (tmp_path / "something-else").mkdir()
        ws = allocate_iteration(tmp_path, "foo")
        assert ws.iteration == 1

    def test_allocate_explicit_iteration_no_collision(self, tmp_path: Path) -> None:
        ws = allocate_iteration(tmp_path, "foo", iteration=5)
        assert ws.iteration == 5
        assert ws.tmp_path.is_dir()

    def test_allocate_explicit_iteration_collision_raises_without_force(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "iteration-5").mkdir()
        with pytest.raises(IterationExistsError, match="iteration-5"):
            allocate_iteration(tmp_path, "foo", iteration=5)

    def test_allocate_explicit_iteration_force_replaces_existing(
        self, tmp_path: Path
    ) -> None:
        existing = tmp_path / "iteration-5"
        existing.mkdir()
        (existing / "stale.txt").write_text("stale")
        ws = allocate_iteration(tmp_path, "foo", iteration=5, force=True)
        assert ws.iteration == 5
        assert not (existing / "stale.txt").exists()
        assert ws.tmp_path.is_dir()

    def test_allocate_force_when_no_existing(self, tmp_path: Path) -> None:
        ws = allocate_iteration(tmp_path, "foo", iteration=2, force=True)
        assert ws.iteration == 2


class TestFinalize:
    def test_finalize_atomic_rename(self, tmp_path: Path) -> None:
        ws = allocate_iteration(tmp_path, "foo")
        (ws.tmp_path / "grading.json").write_text("{}")
        ws.finalize()
        assert ws.final_path.is_dir()
        assert (ws.final_path / "grading.json").read_text() == "{}"
        assert not (tmp_path / "iteration-1-tmp").exists()

    def test_finalize_multiple_iterations(self, tmp_path: Path) -> None:
        ws1 = allocate_iteration(tmp_path, "foo")
        ws1.finalize()
        ws2 = allocate_iteration(tmp_path, "foo")
        ws2.finalize()
        assert (tmp_path / "iteration-1" / "foo").is_dir()
        assert (tmp_path / "iteration-2" / "foo").is_dir()


class TestAbort:
    def test_abort_removes_tmp(self, tmp_path: Path) -> None:
        ws = allocate_iteration(tmp_path, "foo")
        assert (tmp_path / "iteration-1-tmp").exists()
        ws.abort()
        assert not (tmp_path / "iteration-1-tmp").exists()

    def test_abort_safe_when_already_gone(self, tmp_path: Path) -> None:
        ws = allocate_iteration(tmp_path, "foo")
        ws.abort()
        ws.abort()  # Should not raise.


class TestConcurrent:
    def test_concurrent_allocation_threaded(self, tmp_path: Path) -> None:
        n_threads = 5
        barrier = threading.Barrier(n_threads)
        results: list[IterationWorkspace] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                barrier.wait()
                ws = allocate_iteration(tmp_path, "foo")
                with lock:
                    results.append(ws)
            except BaseException as exc:  # pragma: no cover - defensive
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == n_threads
        iterations = sorted(ws.iteration for ws in results)
        assert iterations == list(range(1, n_threads + 1))
        assert len(set(iterations)) == n_threads


class TestSkillNameValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            ".",
            "..",
            "../evil",
            "foo/bar",
            "/abs",
            "with space",
            "has\\backslash",
        ],
    )
    def test_rejects_unsafe_names(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(InvalidSkillNameError):
            validate_skill_name(bad)
        with pytest.raises(InvalidSkillNameError):
            allocate_iteration(tmp_path, bad)

    @pytest.mark.parametrize(
        "good", ["foo", "foo-bar", "foo_bar.v2", "Abc123"]
    )
    def test_accepts_safe_names(self, tmp_path: Path, good: str) -> None:
        assert validate_skill_name(good) == good
        ws = allocate_iteration(tmp_path, good)
        assert ws.iteration == 1


class TestExplicitIterationRobustness:
    def test_rejects_non_positive_iteration(self, tmp_path: Path) -> None:
        for bad in (0, -1, -99):
            with pytest.raises(ValueError, match="iteration must be >= 1"):
                allocate_iteration(tmp_path, "foo", iteration=bad)

    def test_clears_orphan_tmp_from_prior_crash(self, tmp_path: Path) -> None:
        # Prior crashed run left iteration-5-tmp behind. Rerunning
        # --iteration 5 should succeed without --force (tmp is junk).
        orphan = tmp_path / "iteration-5-tmp" / "foo"
        orphan.mkdir(parents=True)
        (orphan / "stale.txt").write_text("junk")

        ws = allocate_iteration(tmp_path, "foo", iteration=5)
        assert ws.iteration == 5
        assert ws.tmp_path.is_dir()
        assert not (ws.tmp_path / "stale.txt").exists()


class TestFinalizeConcurrentRace:
    def test_finalize_raises_iteration_exists_when_target_occupied(
        self, tmp_path: Path
    ) -> None:
        # Simulate a concurrent peer that finalized iteration-1 between our
        # allocation and our finalize() — populate the destination with
        # a non-empty dir so os.rename raises ENOTEMPTY on Linux.
        ws = allocate_iteration(tmp_path, "foo")
        racer_final = tmp_path / "iteration-1" / "foo"
        racer_final.mkdir(parents=True)
        (racer_final / "race.txt").write_text("from peer")

        with pytest.raises(IterationExistsError):
            ws.finalize()

        # After the race, the caller's staging dir is cleaned up.
        assert not (tmp_path / "iteration-1-tmp").exists()
        # The peer's finalized data is untouched.
        assert (racer_final / "race.txt").read_text() == "from peer"
        assert ws.finalized is False
