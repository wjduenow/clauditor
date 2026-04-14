"""Tests for clauditor.workspace — iteration workspace allocator."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from clauditor.workspace import (
    InvalidSkillNameError,
    IterationExistsError,
    IterationWorkspace,
    allocate_iteration,
    stage_inputs,
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


class TestScanMissingDir:
    def test_scan_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        """_scan_existing_iterations should return empty set if dir absent."""
        from clauditor.workspace import _scan_existing_iterations

        missing = tmp_path / "does-not-exist"
        assert _scan_existing_iterations(missing) == set()


class TestAutoRetryExhaustion:
    def test_auto_allocation_raises_after_max_retries(
        self, tmp_path: Path
    ) -> None:
        """_allocate_auto raises RuntimeError after _MAX_AUTO_RETRIES.

        Simulates persistent contention by making every tmp_parent.mkdir
        raise FileExistsError so the loop never converges.
        """
        from clauditor import workspace as ws_mod

        real_mkdir = Path.mkdir
        call_count = {"n": 0}

        def fake_mkdir(self, *args, **kwargs):
            # Let the initial clauditor_dir.mkdir(parents=True,
            # exist_ok=True) and the per-iteration skill-subdir mkdir
            # succeed. Only sabotage tmp_parent.mkdir(exist_ok=False).
            if "-tmp" in self.name and kwargs.get("exist_ok") is False:
                call_count["n"] += 1
                raise FileExistsError(self)
            return real_mkdir(self, *args, **kwargs)

        with (
            patch.object(ws_mod, "_MAX_AUTO_RETRIES", 3),
            patch.object(Path, "mkdir", fake_mkdir),
        ):
            with pytest.raises(RuntimeError, match="exceeded 3 retries"):
                allocate_iteration(tmp_path, "foo")
        assert call_count["n"] == 3

    def test_auto_allocation_skips_finalized_peer(
        self, tmp_path: Path
    ) -> None:
        """Auto loop increments past an existing iteration-N dir.

        Coverage for the final_parent.exists() → candidate+=1 branch
        in _allocate_auto. Forces the branch by injecting an extra
        iteration directory AFTER the initial scan: the scan picks
        candidate=N+1 but by the time the loop runs we've raced in a
        peer N+1, so the first iteration of the loop must skip it.
        """
        (tmp_path / "iteration-5").mkdir()

        # Patch _scan_existing_iterations to return {5} so the loop
        # starts at candidate=6; then create iteration-6 on disk so
        # the loop's exists() check triggers the skip branch.
        from clauditor import workspace as ws_mod

        original_scan = ws_mod._scan_existing_iterations

        def scan_then_race(dir_):
            result = original_scan(dir_)
            (tmp_path / "iteration-6").mkdir(exist_ok=True)
            return result

        with patch.object(
            ws_mod, "_scan_existing_iterations", side_effect=scan_then_race
        ):
            ws = allocate_iteration(tmp_path, "foo")
        assert ws.iteration == 7


class TestFinalizeNonRaceError:
    def test_finalize_reraises_permission_error(
        self, tmp_path: Path
    ) -> None:
        """finalize() must re-raise OSErrors other than ENOTEMPTY/EEXIST.

        Pass 1 review follow-up: a permission-denied rename should
        surface as the real error, not be relabeled as
        IterationExistsError.
        """
        ws = allocate_iteration(tmp_path, "foo")
        eacces = OSError(errno.EACCES, "permission denied")
        with patch("clauditor.workspace.os.rename", side_effect=eacces):
            with pytest.raises(OSError) as exc_info:
                ws.finalize()
        assert exc_info.value.errno == errno.EACCES
        assert ws.finalized is False


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


class TestStageInputs:
    def test_stage_inputs_empty_list_is_noop(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        result = stage_inputs(run_dir, [])
        assert result == []
        assert not (run_dir / "inputs").exists()

    def test_stage_inputs_single_file_copies_content(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        src = tmp_path / "source.txt"
        src.write_bytes(b"hello world")

        result = stage_inputs(run_dir, [src])

        assert len(result) == 1
        assert result[0] == run_dir / "inputs" / "source.txt"
        assert result[0].read_bytes() == b"hello world"

    def test_stage_inputs_preserves_mtime(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        src = tmp_path / "source.txt"
        src.write_bytes(b"content")
        known_mtime = 1_600_000_000.0
        os.utime(src, (known_mtime, known_mtime))

        (dest,) = stage_inputs(run_dir, [src])

        assert abs(dest.stat().st_mtime - known_mtime) < 1.0

    def test_stage_inputs_multiple_files(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        sources = []
        for name, payload in [("a.txt", b"A"), ("b.txt", b"B"), ("c.txt", b"C")]:
            p = tmp_path / name
            p.write_bytes(payload)
            sources.append(p)

        result = stage_inputs(run_dir, sources)

        assert result == [
            run_dir / "inputs" / "a.txt",
            run_dir / "inputs" / "b.txt",
            run_dir / "inputs" / "c.txt",
        ]
        assert result[0].read_bytes() == b"A"
        assert result[1].read_bytes() == b"B"
        assert result[2].read_bytes() == b"C"

    def test_stage_inputs_creates_inputs_subdir_when_missing(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        src = tmp_path / "source.txt"
        src.write_bytes(b"x")
        assert not (run_dir / "inputs").exists()

        stage_inputs(run_dir, [src])

        assert (run_dir / "inputs").is_dir()

    def test_stage_inputs_missing_source_raises_filenotfounderror(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        missing = tmp_path / "nope.txt"

        with pytest.raises(FileNotFoundError, match="nope.txt"):
            stage_inputs(run_dir, [missing])
