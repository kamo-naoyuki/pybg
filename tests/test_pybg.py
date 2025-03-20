import os
import shlex
import shutil
import signal
import time
import uuid
from multiprocessing import Process
from pathlib import Path

import pytest

from pybg import get_jobid, main


@pytest.mark.parametrize(
    "sbatch_options",
    [
        "none",
        pytest.param("", marks=pytest.mark.xfail(shutil.which("sbatch") is None, reason="Require slurm")),
    ],
)
def test(capsys, sbatch_options):
    gid = uuid.uuid4().hex
    command = ["echo", "Hello"]
    main(["start", gid])
    main(["add", "--sbatch-options", sbatch_options, gid] + command)

    # Dupliczted
    main(["add", "--sbatch-options", sbatch_options, gid, "ls"])
    main(["add", "--sbatch-options", sbatch_options, gid, "ls"])

    main(["dump", gid])
    main(["run", gid])

    jobid = get_jobid(shlex.join(command))

    capsys.readouterr()

    main(["show", gid])
    captured = capsys.readouterr()
    assert jobid in captured.out

    main(["show", gid, "s"])
    captured = capsys.readouterr()
    assert jobid in captured.out

    main(["show", gid, jobid])
    captured = capsys.readouterr()
    assert "Hello\n" in captured.out

    main(["show", gid, jobid, "batch_script"])
    captured = capsys.readouterr()
    if sbatch_options == "none":
        assert "is not existing" in captured.out

    main(["show-server-log", "--line", "10", gid])


def test_tpl():
    main(["tpl"])


def test_retry(capsys):
    gid = uuid.uuid4().hex
    fi = "pybg_logs/first"
    main(["start", gid])
    main(
        [
            "add",
            gid,
            "bash",
            "-c",
            f"[[ -e {fi} ]] && {{ rm -f {fi}; exit 0; }} || {{ touch {fi}; exit 1; }}",
        ]
    )
    main(["dump", gid])
    with pytest.raises(SystemExit):
        main(["run", "--retry", "0", gid])

    Path(fi).unlink()
    capsys.readouterr()
    main(["run", "--retry", "2", gid, "n"])
    captured = capsys.readouterr()
    assert "2nd retry" in captured.out


def test_fail_command(capsys):
    gid = uuid.uuid4().hex
    command = ["exit", "1"]
    main(["start", gid])
    main(["add", gid] + command)
    main(["dump", gid])
    with pytest.raises(SystemExit):
        main(["run", gid])

    jobid = get_jobid(shlex.join(command))
    main(["show", gid, "f"])
    captured = capsys.readouterr()
    assert jobid in captured.out


def test_server_is_not_runnnig_add():
    gid = uuid.uuid4().hex
    with pytest.raises(RuntimeError):
        main(["add", gid, "echo"])


def test_server_is_not_runnnig_dump():
    gid = uuid.uuid4().hex
    with pytest.raises(RuntimeError):
        main(["dump", gid])


def sleep_jobs(sbatch_options):
    gid = uuid.uuid4().hex

    sbatch_options = ""
    main(["start", gid])
    main(["add", "--sbatch-options", sbatch_options, gid, "sleep", "100"])
    main(["add", gid, "sleep", "100"])
    main(["dump", gid])
    main(["run", gid])


@pytest.mark.parametrize(
    "sbatch_options",
    [
        pytest.param("", marks=pytest.mark.xfail(shutil.which("sbatch") is None, reason="Require slurm")),
    ],
)
@pytest.mark.parametrize(
    "_signal",
    [signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGQUIT],
)
def test_kill(capsys, _signal, sbatch_options):
    process = Process(target=sleep_jobs, kwargs={"sbatch_options": sbatch_options})
    process.start()

    time.sleep(2)
    os.kill(process.pid, _signal)

    process.join()

    assert process.exitcode != 0


def test_server_idle_timeout(capsys):
    gid = uuid.uuid4().hex

    main(["start", "--server-idle-timeout", "0.01", gid])

    time.sleep(8)

    capsys.readouterr()
    main(["show-server-log", "--line", "1", gid])
    captured = capsys.readouterr()
    assert "Timeout" in captured.out


def test_help():
    main([])
