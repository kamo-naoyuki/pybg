import argparse
import datetime
import getpass
import hashlib
import logging
import multiprocessing
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from functools import partial, wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

__version__ = "0.1.0"

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
FORMAT = "%(levelname)-9s  %(asctime)s [%(filename)s:%(lineno)d] %(message)s"
st_handler = logging.StreamHandler()
st_handler.setFormatter(logging.Formatter(FORMAT))
logger.addHandler(st_handler)

DFMT = "%Y/%m/%d %H:%M:%S"
JOBID_LEN = 9


def ordinal(n: int) -> str:
    """
    Convert an integer to its ordinal representation (e.g., 1 -> '1st', 2 -> '2nd', 3 -> '3rd', 4 -> '4th').
    """
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

    return f"{n}{suffix}"


def print_colored(text, *, color=None, **kwargs) -> None:
    use_color = sys.stdout.isatty()
    if use_color and color is not None:
        print(color + text + RESET, **kwargs, flush=True)
    else:
        print(text, **kwargs, flush=True)


class LockPrint:
    def __init__(self):
        self.lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self.lock:
            print_colored(*args, **kwargs)


def read_file_reverse(filename: Union[str, Path], max_lines: int):
    lines = []
    with open(filename, "rb") as f:
        f.seek(0, 2)
        position = f.tell()
        buffer: List[bytes] = []
        line_count = 0

        while position > 0 and line_count < max_lines:
            position -= 1
            f.seek(position)
            char = f.read(1)

            if char == b"\n":
                if len(buffer) > 0:
                    lines.append(b"".join(reversed(buffer)).decode())
                    buffer = []
                    line_count += 1
                    if line_count >= max_lines:
                        break
            else:
                buffer.append(char)

        if len(buffer) > 0 and line_count < max_lines:
            lines.append(b"".join(reversed(buffer)).decode("utf-8"))
    for line in lines[::-1]:
        print(line)


class PROTO:
    ACK = b"\1"
    ADD = b"\2"
    CLEAR = b"\3"
    DUMP = b"\4"
    END = b"\5"


def with_coverage():
    import coverage

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cov = None
            if "COV_CORE_SOURCE" in os.environ:
                data_file = os.environ["COV_CORE_DATAFILE"]
                cov = coverage.Coverage(data_file=data_file, branch=True)
                cov.start()

            try:
                result = func(*args, **kwargs)
            finally:
                if cov:
                    cov.stop()
                    cov.save()

            return result

        return wrapper

    return decorator


class CommandPoolServer:
    @classmethod
    def get_socket(cls) -> socket.socket:
        if os.name == "posix":
            return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    @classmethod
    def get_socket_file(cls, group_id: str) -> str:
        if os.name == "posix":
            return f"/tmp/pybg-{getpass.getuser()}/{group_id}.socket"
        else:
            raise RuntimeError("Only UNIX is supported")
            if isinstance(group_id, int):
                raise RuntimeError(f"group_id must be a port number for Windows: {group_id}")
            return ("127.0.0.1", group_id)

    @classmethod
    def get_log_file(cls, group_id: str) -> str:
        return f"/tmp/pybg-{getpass.getuser()}/{group_id}.log"

    def __init__(self, group_id, timeout=60.0 * 5):
        self.group_id = group_id
        self.socket_file = CommandPoolServer.get_socket_file(group_id)
        Path(self.socket_file).parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.last_access_time = time.time()
        self.running = True
        self.lock = threading.Lock()
        self.log_file = CommandPoolServer.get_log_file(group_id)
        self.commands = []

    def daemonize(self) -> None:
        if os.fork() > 0:
            sys.exit()
        os.setsid()
        if os.fork() > 0:
            sys.exit()
        sys.stdin = open("/dev/null")
        Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(self.log_file, "a+")
        sys.stderr = open(self.log_file, "a+")

    def is_port_in_use(self) -> bool:
        with CommandPoolServer.get_socket() as s:
            return s.connect_ex(self.socket_file) == 0

    def timeout_checker(self, time_interval=5):
        while self.running:
            time.sleep(time_interval)
            with self.lock:
                if time.time() - self.last_access_time > self.timeout:
                    self.running = False
                    logger.warning(f"Timeout: {self.timeout}s")
                    os._exit(0)

    def safe_recv(self, socket: socket.socket, chunk: int = 1024) -> Optional[bytes]:
        received = b""
        while True:
            try:
                data = socket.recv(chunk)
                with self.lock:
                    self.last_access_time = time.time()
                received += data
                if data.endswith(PROTO.END):
                    break

            except ConnectionResetError as e:
                # Connection reset by client should be ignored
                logger.error(f"{e}")
                data = None
                return None
            except MemoryError:
                logger.error("Memory error has happened! Stopping server...")
                self.running = False
                data = None
                return None
            if not data:
                break
        return received

    def safe_sendall(self, socket: socket.socket, data: bytes) -> bool:
        try:
            socket.sendall(data)
            with self.lock:
                self.last_access_time = time.time()
            return True
        except ConnectionResetError as e:
            logger.error(f"Error sending: {e}")
        except BrokenPipeError as e:
            logger.error(f"Error sending: {e}")
        return False

    def start(self) -> None:
        self.daemonize()
        self._start()

    # To measure coverage with daemon process
    @with_coverage()
    def _start(self):
        logger.removeHandler(st_handler)
        st_handler.close()
        new_file_handler = RotatingFileHandler(
            CommandPoolServer.get_log_file(self.group_id),
            maxBytes=1024 * 1024,
            backupCount=1,
        )
        new_file_handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(new_file_handler)
        logger.info(f"Server has started: {self.group_id}")

        timeout_thread = threading.Thread(target=self.timeout_checker, daemon=True)
        timeout_thread.start()

        with CommandPoolServer.get_socket() as server:
            server.bind(self.socket_file)
            server.listen()

            while self.running:
                logger.info("Waiting for connection...")
                client_socket, addr = server.accept()
                with self.lock:
                    self.last_access_time = time.time()

                logger.info(f"Connection has been accepted: {addr}")
                data = self.safe_recv(client_socket)
                if data is not None:
                    if data.startswith(PROTO.CLEAR):
                        logger.info("RECV: CLEAR")
                        self.safe_sendall(client_socket, PROTO.ACK + PROTO.END)
                        self.commands = []

                    elif data.startswith(PROTO.ADD):
                        command = data[len(PROTO.ADD) : -len(PROTO.END)]
                        logger.info(f"RECV: ADD: {command}")
                        self.safe_sendall(client_socket, PROTO.ACK + PROTO.END)
                        self.commands.append(command)

                    elif data.startswith(PROTO.DUMP):
                        logger.info("RECV: DUMP")
                        ack_data = None
                        while True:
                            for command in self.commands:
                                if not self.safe_sendall(client_socket, command + b"\n"):
                                    break
                            if not self.safe_sendall(client_socket, PROTO.END):
                                break
                            ack_data = self.safe_recv(client_socket)
                            if ack_data == PROTO.ACK + PROTO.END:
                                break
                            elif ack_data is not None:
                                logger.error("ACK packet is expected: {ack_data}")

                        with self.lock:
                            logger.error("Successfully finished writing. Stopping server...")
                            self.running = False

                    elif data:
                        logger.error(f"Unknown format: {data}")
                    else:
                        pass


def check_server_running(group_id: str, timeout: float = 10) -> None:
    socket_file = CommandPoolServer.get_socket_file(group_id)
    # Wait until the server has started
    start_time = time.time()
    with CommandPoolServer.get_socket() as client:
        while client.connect_ex(socket_file) != 0:
            if time.time() - start_time > timeout:
                raise RuntimeError(f"Server '{group_id}' is not running? Timeout: {timeout}s")
            time.sleep(0.01)


def server_start(group_id: str, server_idle_timeout: float = 60 * 5, check_server_timeout: float = 10) -> None:
    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        if client.connect_ex(socket_file) != 0:
            if os.name == "posix":
                try:
                    Path(socket_file).unlink()
                except FileNotFoundError:
                    pass

            server = CommandPoolServer(group_id=group_id, timeout=server_idle_timeout)
            process = multiprocessing.Process(target=server.start, daemon=True)
            process.start()

    check_server_running(group_id, timeout=check_server_timeout)


def dump(
    group_id: str, basedir: Optional[Union[str, Path]] = None, check_server_timeout: float = 1, allow_same: bool = False
):
    check_server_running(group_id, timeout=check_server_timeout)

    if basedir is None:
        basedir = os.environ.get("PYBG_BASEDIR", "pybg_logs")
    basedir = Path(basedir)
    groupdir = basedir / group_id
    groupdir.mkdir(parents=True, exist_ok=True)

    added_commands = set()

    socket_file = CommandPoolServer.get_socket_file(group_id)
    with (groupdir / "commands").open("w") as fout, CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.DUMP + PROTO.END)

        received = b""
        count = 0
        while True:
            data = client.recv(1024)
            if not data:
                raise RuntimeError("Server is shutdown")

            binary_command_list = []
            data_list = data.split(b"\n")
            for idx, _data in enumerate(data_list[:-1]):
                if idx == 0:
                    concat = received + _data
                    received = b""
                else:
                    concat = _data
                binary_command_list.append(concat)

            received += data_list[-1]

            for command in binary_command_list:
                decoded_command = command.decode()
                if "#SBATCH" in decoded_command:
                    valid_command, sbatch_options = decoded_command.split("#SBATCH", maxsplit=1)
                else:
                    valid_command = decoded_command
                valid_command = valid_command.strip()

                if valid_command not in added_commands:
                    fout.write(f"{decoded_command}\n")
                    count += 1
                else:
                    print_colored(
                        log_format(
                            message=f"Duplicated commands: {valid_command}",
                            group_id=group_id,
                        )
                    )

                if not allow_same:
                    added_commands.add(valid_command)

            if PROTO.END in received:
                if received != PROTO.END:
                    raise RuntimeError(f"Bug?: {received!r}")
                # Break while loop
                print_colored(
                    log_format(
                        message=f"Received {count} commands from the server"
                        f"({socket_file}) and dumped in {(groupdir / 'commands').absolute()}",
                        group_id=group_id,
                    )
                )
                return
        client.sendall(PROTO.ACK + PROTO.END)


def log_format(
    message: str,
    group_id=None,
    jobid=None,
    status=None,
) -> str:
    retval = f"[ {datetime.datetime.now():{DFMT}}"
    if group_id and jobid is not None:
        retval += f" | {group_id} {jobid}"
    elif group_id is not None:
        retval += f" | {group_id}"
    if status is not None:
        retval += f" | {status}"

    retval += f" ] {message}"
    return retval


def get_jobid(command):
    jobid = hashlib.sha256(command.encode()).hexdigest()
    # Using the first 8 chars for usabilily
    jobid = jobid[:JOBID_LEN]
    return jobid


def yield_command_list(groupdir: Path, slurm_check: bool = False):
    if (groupdir / "commands").exists():
        with (groupdir / "commands").open("r") as f:
            command_list = [line.strip() for line in f]
    else:
        raise RuntimeError(f"{str(groupdir / 'commands')} is not existing")

    counter: Counter = Counter()
    for command in command_list:
        if "#SBATCH" in command:
            if slurm_check:
                for scommand in ["sbatch", "squeue", "scancel"]:
                    if shutil.which(scommand) is None:
                        raise RuntimeError(f"Slurm is not setup? command not found: {scommand}")
            valid_command, sbatch_options = command.split("#SBATCH", maxsplit=1)
            sbatch_options = sbatch_options.strip()
        else:
            valid_command = command
            sbatch_options = None
        valid_command = valid_command.strip()
        counter[valid_command] += 1

        jobid = get_jobid(valid_command + "" if counter[valid_command] == 1 else str(counter[valid_command]))
        jobdir = groupdir / jobid
        status_text = jobdir / "status"
        if status_text.exists():
            try:
                status = int(status_text.read_text())
            except ValueError:
                status = None
        else:
            status = None

        yield status, command, valid_command, jobid, sbatch_options


class Runner:
    def __init__(self):
        self.subprocesses = []

    @staticmethod
    def squeue(slurm_jobid):
        process = subprocess.Popen(
            [
                "squeue",
                "--noheader",
                "--job",
                str(slurm_jobid),
                "-o",
                "%T",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate()
        stdout = stdout.decode()
        stderr = stderr.decode()
        # Job already finished
        if (
            process.returncode != 0
            or stdout == ""
            or stdout.strip() in ["COMPLETING", "CANCELLED", "FAILED", "TIMEOUT"]
        ):
            return False
        else:
            return True

    def write_status(
        self,
        group_id: str,
        failed_jobs,
        processes,
        success_fail_counter: List[int],
        event: threading.Event,
        waittime: float = 0.02,
        squeue_minimum_interval: float = 2.0,
        retry: int = 0,
    ):
        last_squeue_time = None
        while True:
            with self.lock:
                _processes = processes.copy()
            for jobid, (
                process,
                command,
                valid_command,
                jobdir,
                sbatch_options,
                slurm_jobid,
                submit_counter,
            ) in _processes.items():
                returncode = None
                if slurm_jobid is None:
                    try:
                        process.wait(waittime)
                    except subprocess.TimeoutExpired:
                        pass
                    else:
                        returncode = process.returncode
                else:
                    # Force sync
                    list(jobdir.iterdir())
                    # status file is wrote by batch script itself
                    if (jobdir / "status").exists():
                        try:
                            returncode = int((jobdir / "status").read_text().strip())
                        except Exception:
                            returncode = 1
                    else:
                        # If active_text is existing, check the time interval instead of squeue command
                        # to reduce the load of slurm server
                        if (jobdir / "active").exists():
                            time_from_last_update = time.time() - (jobdir / "active").stat().st_mtime
                            # Double check using squeue command
                            if time_from_last_update > 2.5 and not self.squeue(slurm_jobid):
                                last_squeue_time = time.time()
                                # Force sync
                                list(jobdir.iterdir())

                                # the job has been Killed
                                if not (jobdir / "status").exists():
                                    (jobdir / "status").write_text("1\n")
                                    returncode = 1
                                else:
                                    try:
                                        returncode = int((jobdir / "status").read_text().strip())
                                    except Exception:
                                        returncode = 1

                        # if active_text is not existing, the job has not yet started
                        else:
                            # To reduce the load of slurm server,
                            # avoid excecuting squeue command within squeue_minimum_interval
                            if last_squeue_time is None or time.time() - last_squeue_time > squeue_minimum_interval:
                                last_squeue_time = time.time()

                                # Job already finished
                                if not self.squeue(slurm_jobid):
                                    # Force sync
                                    list(jobdir.iterdir())

                                    # the job has been Killed
                                    if not (jobdir / "status").exists():
                                        (jobdir / "status").write_text("1\n")
                                        returncode = 1
                                    else:
                                        try:
                                            returncode = int((jobdir / "status").read_text().strip())
                                        except Exception:
                                            returncode = 1

                if returncode is not None:
                    (Path(jobdir) / "status").write_text(f"{returncode}\n")
                    if returncode != 0:
                        self.lock_print(
                            log_format(
                                message=command,
                                group_id=group_id,
                                jobid=jobid,
                                status=f"Fail({returncode})",
                            ),
                            color=RED,
                        )

                        if retry == -1 or retry >= submit_counter:
                            self.submit(
                                group_id,
                                command,
                                valid_command,
                                jobdir,
                                sbatch_options,
                                processes,
                                success_fail_counter,
                                submit_counter + 1,
                            )
                        else:
                            with self.count_lock:
                                success_fail_counter[1] += 1

                            failed_jobs.add(jobid)
                            with self.lock:
                                del processes[jobid]
                    else:
                        self.lock_print(
                            log_format(
                                message=command,
                                group_id=group_id,
                                jobid=jobid,
                                status="Success",
                            ),
                            color=GREEN,
                        )
                        with self.count_lock:
                            success_fail_counter[0] += 1

                        with self.lock:
                            del processes[jobid]

            time.sleep(waittime)

            if event.is_set():
                break

    def submit(
        self,
        group_id: str,
        command: str,
        valid_command: str,
        jobdir: Union[str, Path],
        sbatch_options: str,
        processes,
        success_fail_counter: List[int],
        submit_counter: int,
    ):
        jobdir = Path(jobdir)
        logfile = jobdir / "output"
        jobid = jobdir.name

        if sbatch_options is None:
            flogfile = logfile.open("w")
            pid_text = jobdir / "pid"
            try:
                pid_text.unlink()
            except FileNotFoundError:
                pass

            try:
                process = subprocess.Popen(shlex.split(valid_command), stdout=flogfile, stderr=flogfile)
            except FileNotFoundError:
                flogfile.write(traceback.format_exc() + "\n")
                self.lock_print(
                    log_format(
                        message=f"Failed to execute command: {valid_command}",
                        group_id=group_id,
                        jobid=jobid,
                        status="Error",
                    ),
                    color=RED,
                )
                with self.count_lock:
                    success_fail_counter[1] += 1
            else:
                pid_text.write_text(f"{process.pid}\n")
                slurm_jobid = None
                self.subprocesses.append((process, slurm_jobid))

                if submit_counter > 1:
                    resubmit_str = f", {ordinal(submit_counter)} retry"
                else:
                    resubmit_str = ""

                self.lock_print(
                    log_format(
                        message=command,
                        group_id=group_id,
                        jobid=jobid,
                        status=f"Submit(pid={process.pid}){resubmit_str}",
                    ),
                )
                with self.lock:
                    processes[jobid] = (
                        process,
                        command,
                        valid_command,
                        jobdir,
                        sbatch_options,
                        slurm_jobid,
                        submit_counter,
                    )

        else:
            # Using sbatch (Slurm batch script)
            slurm_job_name = f"pybg show {group_id} {jobid}"
            sbatch_command = ["sbatch", f"--output={logfile}", f"--job-name={slurm_job_name}"] + shlex.split(
                sbatch_options
            )
            process = subprocess.Popen(
                sbatch_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            active_text = jobdir / "active"
            status_text = jobdir / "status"
            slurm_jobid_text = jobdir / "slurm_jobid"
            for p in [active_text, status_text, slurm_jobid_text]:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            batch_script = f"""\
#!/usr/bin/env sh
update_timestamp() {{
    while true; do
        touch '{active_text}'
        sleep 1
    done

}}
write_status() {{
    echo $? > '{status_text}'
    kill $PID
    exit $?
}}
# For logging
echo "##############################################################"
echo "# $(date)"
echo "# $(hostname)"
echo "# $(pwd)"
echo '# {sbatch_options}'
echo "##############################################################"

rm -f '{status_text}'
# Force sync
ls '{jobdir}' > /dev/null 2>&1

update_timestamp &
PID=$!
trap write_status INT
trap write_status TERM
trap write_status QUIT
trap write_status EXIT

{valid_command}
"""
            (jobdir / "batch_script").write_text(batch_script)
            stdout, stderr = process.communicate(batch_script.encode())
            # Failed to submit
            if process.returncode != 0:
                self.lock_print(
                    log_format(
                        message=f"{shlex.join(sbatch_command)}: {stderr.decode()}",
                        group_id=group_id,
                        jobid=jobid,
                        status="Error",
                    ),
                    color=RED,
                )
                with self.count_lock:
                    success_fail_counter[1] += 1
            else:

                def get_jobid(stdout):
                    # Derive jobid from output
                    # e.g. Submitted batch job 1346323
                    slurm_jobid = None
                    for token in stdout.decode().strip().split():
                        try:
                            _slurm_jobid = int(token)
                        except ValueError:
                            pass
                        else:
                            if slurm_jobid is None:
                                slurm_jobid = _slurm_jobid
                            else:
                                slurm_jobid = None
                                break
                    return slurm_jobid

                slurm_jobid = get_jobid(stdout)

                if slurm_jobid is None:
                    self.lock_print(
                        log_format(
                            message=f"Unexpected output from sbatch: {stderr.decode()}",
                            group_id=group_id,
                            jobid=jobid,
                            status="Error",
                        ),
                    )
                    with self.count_lock:
                        success_fail_counter[1] += 1
                else:
                    slurm_jobid_text.write_text(f"{slurm_jobid}\n")
                    self.subprocesses.append((None, slurm_jobid))
                    jobid = Path(jobdir).name
                    with self.lock:
                        processes[jobid] = (
                            None,
                            command,
                            valid_command,
                            jobdir,
                            sbatch_options,
                            slurm_jobid,
                            submit_counter,
                        )

                    if submit_counter > 1:
                        resubmit_str = f", {ordinal(submit_counter)} retry"
                    else:
                        resubmit_str = ""

                    self.lock_print(
                        log_format(
                            message=command,
                            group_id=group_id,
                            jobid=jobid,
                            status=f"Submit(slurm_jobid={slurm_jobid}){resubmit_str}",
                        ),
                    )

    def run_process(
        self,
        group_id: str,
        processes,
        jobs,
        success_fail_counter: List[int],
        event: threading.Event,
        log_interval: float = 300.0,
        num_parallel: int = 10,
        launch_interval: float = 0.05,
    ):
        st = time.time()
        while True:
            with self.lock:
                running = len(processes)
            if len(jobs) > 0 and running <= num_parallel:
                command, valid_command, jobdir, sbatch_options = jobs.pop()
                self.submit(
                    group_id, command, valid_command, jobdir, sbatch_options, processes, success_fail_counter, 1
                )

            # All jobs have been finished
            if len(jobs) == 0 and len(processes) == 0:
                event.set()
                break

            if time.time() - st > log_interval:
                with self.count_lock:
                    succeeded, failed = success_fail_counter
                queue = len(jobs)
                with self.lock:
                    running = len(processes)

                self.lock_print(
                    log_format(
                        message=f"queue/running/success/fail = {queue}/{running}/{succeeded}/{failed}",
                        group_id=group_id,
                        status="Status",
                    ),
                )
                st = time.time()
            time.sleep(launch_interval)

    def run(
        self,
        group_id,
        jobids=List[str],
        basedir: Optional[Union[str, Path]] = None,
        num_parallel=10,
        launch_interval=0.1,
        waittime=0.02,
        log_interval=30,
        retry: int = 0,
    ):

        if basedir is None:
            basedir = os.environ.get("PYBG_BASEDIR", "pybg_logs")
        basedir = Path(basedir)
        groupdir = basedir / group_id

        self.lock_print = LockPrint()
        self.lock_print(
            log_format(
                message=f"basedir={basedir}, retry={retry}, num_parallel={num_parallel}",
                group_id=group_id,
                status="Start",
            ),
        )
        specified_jobids = [jobid for jobid in jobids if len(jobid) == JOBID_LEN]

        jobs = []
        num_skipped = 0
        for status, command, valid_command, jobid, sbatch_options in yield_command_list(groupdir, slurm_check=True):
            jobdir = groupdir / jobid
            jobdir.mkdir(parents=True, exist_ok=True)
            command_text = jobdir / "command"
            command_text.write_text(valid_command + "\n")
            jobdir / "status"
            sbatch_options_text = jobdir / "sbatch_options"
            if sbatch_options is not None:
                sbatch_options_text.write_text(sbatch_options)
            else:
                try:
                    sbatch_options_text.unlink()
                except FileNotFoundError:
                    pass
            for p in [jobdir / "pid", jobdir / "slurm_jobid"]:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass

            if (
                len(jobids) == 0
                or any(
                    (
                        (_jobid in ["unfinish", "nonsuccess"] and status is None)
                        or (_jobid in ["fail", "nonsuccess"] and status > 0)
                        or (_jobid == "success" and status == 0)
                    )
                    for _jobid in jobids
                )
                or jobid in specified_jobids
            ):
                jobs.append((command, valid_command, jobdir, sbatch_options))

                if jobid in specified_jobids:
                    specified_jobids.remove(jobid)
            else:
                num_skipped += 1

        self.lock_print(log_format(message=f"{num_skipped} jobs have been skipped", group_id=group_id))
        for jobid in specified_jobids:
            self.lock_print(
                log_format(message=f"Warning: Jobid '{jobid}' is not existing", group_id=group_id), color=RED
            )

        self.lock = threading.Lock()
        self.count_lock = threading.Lock()
        success_fail_counter = [0, 0]
        event = threading.Event()
        processes: Dict[str, Any] = {}
        failed_jobs: Set[str] = set()

        thread = threading.Thread(
            target=self.write_status,
            kwargs={
                "group_id": group_id,
                "processes": processes,
                "failed_jobs": failed_jobs,
                "success_fail_counter": success_fail_counter,
                "event": event,
                "waittime": waittime,
                "retry": retry,
            },
            daemon=True,
        )

        thread.start()
        self.run_process(
            group_id=group_id,
            processes=processes,
            jobs=jobs,
            success_fail_counter=success_fail_counter,
            event=event,
            log_interval=log_interval,
            num_parallel=num_parallel,
            launch_interval=launch_interval,
        )
        thread.join()

        succeeded, failed = success_fail_counter
        self.lock_print(
            log_format(
                message=f"success/fail/skip = {succeeded}/{failed}/{num_skipped}",
                group_id=group_id,
                status="End",
            ),
            color=RED if failed != 0 else GREEN,
        )

        if failed != 0:
            self.lock_print(
                f"To submit only failed jobs: pybg run --basedir {Path(basedir).absolute()} {group_id} nonsuccess",
                color=RED,
            )
            self.lock_print(f"Failed jobids: {' '.join(failed_jobs)}", color=RED)
            sys.exit(1)


def start_handler(args: argparse.Namespace):
    start_and_start(group_id=args.group_id, server_idle_timeout=args.server_idle_timeout)


def start_and_start(group_id: str, server_idle_timeout: float = 60.0 * 5, check_server_timeout: float = 2.0):
    server_start(group_id, server_idle_timeout=server_idle_timeout, check_server_timeout=check_server_timeout)

    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.CLEAR + PROTO.END)
        data = client.recv(1024)
        if data != PROTO.ACK + PROTO.END:
            print_colored(
                log_format(
                    message=f"Received unexpected data: {data!r}",
                    group_id=group_id,
                    status="Error",
                ),
            )
            sys.exit(1)


def add_handler(args: argparse.Namespace):
    command = shlex.join(args.command)
    if args.sbatch_options is not None:
        command += f" #SBATCH {args.sbatch_options}"
    add(group_id=args.group_id, command=command)


def add(group_id: str, command: str, check_server_timeout: float = 2.0):
    check_server_running(group_id, timeout=check_server_timeout)

    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.ADD + command.encode() + PROTO.END)
        data = client.recv(1024)
        if data != PROTO.ACK + PROTO.END:
            print_colored(
                log_format(
                    message=f"Received unexpected data: {data!r}",
                    group_id=group_id,
                    status="Error",
                ),
            )
            sys.exit(1)


def dump_handler(args: argparse.Namespace):
    dump(group_id=args.group_id, basedir=args.basedir, allow_same=args.allow_same)


def run_handler(args: argparse.Namespace):
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    runner = Runner()
    try:
        runner.run(
            group_id=args.group_id,
            basedir=args.basedir,
            jobids=args.jobids,
            num_parallel=args.num_parallel,
            launch_interval=args.launch_interval,
            waittime=args.waittime,
            log_interval=args.log_interval,
            retry=args.retry,
        )
    except (SystemExit, KeyboardInterrupt) as e:
        # Ignore stopping signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGQUIT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        stop_slurm_jobs(runner.subprocesses)
        if isinstance(e, KeyboardInterrupt):
            # Basically, we don't need to send KeyboardInterrupt for subprocesses.
            # This is done for srun command.
            stop_subprocesses(runner.subprocesses)

        # Restore stopping signal
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        raise e


def signal_handler(signum, frame) -> None:
    print(f"Signal {signum} received...", flush=True)
    sys.exit(128 + signum)


def stop_subprocesses(processes, timeout=15):
    first = True
    for p, _ in processes:
        if p is not None:
            if first:
                print("Sending KeyboardInterrupt...", flush=True)
                first = False

            # NOTE: send_signal is non-blocking
            # NOTE: Do nothing if the process completed.
            p.send_signal(signal.SIGINT)

    for p, _ in processes:
        if p is not None:
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                print_colored(
                    log_format(message=f"Process(pid={p.pid}) has not yet been stopped. Killing the process..."),
                    color=RED,
                )
                p.kill()
    # No wait for killing


def stop_slurm_jobs(processes, timeout=15):
    first = True
    for _, slurm_jobid in processes:
        if slurm_jobid is not None:
            if first:
                print("Cancelling Slurm jobs...", flush=True)
                first = False
            subprocess.run(["scancel", f"{slurm_jobid}"])


def show_handler(args: argparse.Namespace):
    basedir = args.basedir
    group_id = args.group_id
    jobid = args.jobid
    filename = args.filename
    nofilepath = args.nofilepath
    groupdir = Path(basedir) / group_id
    if jobid is None or jobid in ["nonsuccess", "unfinish", "fail", "success"]:
        if filename is not None:
            print("Warning: filename has been ignored: {filename}")

        print(
            " ".join(
                _jobid
                for status, command, valid_command, _jobid, sbatch_options in yield_command_list(groupdir)
                if jobid is None
                or (jobid in ["unfinish", "nonsuccess"] and status is None)
                or (jobid in ["fail", "nonsuccess"] and status is not None and status != 0)
                or (jobid == "success" and status == 0)
            )
        )
        return

    if filename is None:
        filename = "output"

    jobdir = Path(basedir) / group_id / jobid
    if (jobdir / filename).exists():
        print((jobdir / filename).read_text(), end="")
        if not nofilepath:
            print(f"( {jobdir / filename} )")
        return
    else:
        print(f"{jobdir / filename} is not existing")
        return


def show_server_log_handler(args: argparse.Namespace):
    logfile = Path(CommandPoolServer.get_log_file(args.group_id))
    if logfile.exists():
        read_file_reverse(logfile, args.line)
        print(f"( {logfile} )")
    else:
        print(f"(Logfile is not existing: {logfile})")


def tpl_handler(args, parser_start, parser_add, parser_dump, parser_run):
    print(
        f"""#!/usr/bin/env bash
group_id='{"group_id" if args.group_id is None else args.group_id}'
# export PYBG_BASEDIR='pybg_logs'

pybg start "${{group_id}}"

pybg add "${{group_id}}" [write command]

pybg dump "${{group_id}}"
pybg run --retry '{parser_run.get_default("retry")}' \
--num-parallel '{parser_run.get_default("num_parallel")}' \
--log-interval '{parser_run.get_default("log_interval")}' \
"${{group_id}}"\
"""
    )


def jobid_str(arg) -> str:
    order_types = ["nonsuccess", "unfinish", "fail", "success"]
    if len(arg) == JOBID_LEN:
        return arg
    for order_type in order_types:
        if order_type.startswith(arg):
            return order_type
    else:
        raise TypeError("Must be {JOBID_LEN} length string or one of {', '.join(order_types)}: {arg}")


def str_or_none(arg) -> Optional[str]:
    if arg.lower() in ["none", "null"]:
        return None
    else:
        return arg


def str2bool(arg) -> bool:
    if arg.lower() in ["true", "1"]:
        return True
    elif arg.lower() in ["false", "0"]:
        return False
    else:
        raise TypeError(f"Must be true or false, but got {arg}")


def main(sysargs: Optional[List[str]] = None):
    for order_type in ["nonsuccess", "unfinish", "fail", "success"]:
        if len(order_type) == JOBID_LEN:
            raise RuntimeError(f"Please set different number: JOBID_LEN={JOBID_LEN}")

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    subparsers = parser.add_subparsers()

    parser_start = subparsers.add_parser("start")
    parser_start.add_argument("group_id")
    parser_start.add_argument("--server-idle-timeout", type=float, default=60.0 * 5)
    parser_start.set_defaults(handler=start_handler)

    parser_add = subparsers.add_parser("add")
    parser_add.add_argument(
        "--sbatch-options", "-s", type=str_or_none, help="When this option is used, jobs can be submitted using sbatch"
    )
    parser_add.add_argument("group_id")
    parser_add.add_argument("command", nargs=argparse.REMAINDER)
    parser_add.set_defaults(handler=add_handler)

    parser_dump = subparsers.add_parser("dump")
    parser_dump.add_argument("group_id")
    parser_dump.add_argument("--basedir", default=None)
    parser_dump.add_argument(
        "--allow-same", "-a", action="store_true", help="Specifies whether to register the same command"
    )
    parser_dump.set_defaults(handler=dump_handler)

    parser_run = subparsers.add_parser("run")
    parser_run.add_argument("group_id")
    parser_run.add_argument("jobids", type=jobid_str, nargs="*", help="jobids or nonsuccess/fail/success/unfinish")
    parser_run.add_argument("--basedir", default=None)
    parser_run.add_argument(
        "--num-parallel", "-n", default=50, type=int, help="The maximum number of jobs executed in parallel at a time"
    )
    parser_run.add_argument(
        "--launch-interval",
        default=0.05,
        type=float,
        help="To reduce the load, waits for the specified number of seconds each time a job is submitted",
    )
    parser_run.add_argument("--waittime", default=0.1, type=float)
    parser_run.add_argument(
        "--log-interval",
        default=300.0,
        type=float,
        help="Displays the status of the submitted jobs at specified intervals",
    )
    parser_run.add_argument(
        "--retry",
        default=0,
        type=int,
        help="Specifies the maximum number of times to resubmit a job when it fails."
        "If set to 0, the job will not be resubmitted.  "
        "If set to -1, resubmission will continue indefinitely.",
    )
    parser_run.set_defaults(handler=run_handler)

    parser_show = subparsers.add_parser("show", help="Shows the output or status of jobs")
    parser_show.add_argument("--nofilepath", type=str2bool, default=False)
    parser_show.add_argument("--basedir", default="pybg_logs")
    parser_show.add_argument("group_id")
    parser_show.add_argument("jobid", type=jobid_str, nargs="?", help="jobids or nonsuccess/fail/success/unfinish")
    parser_show.add_argument(
        "filename",
        nargs="?",
        choices=["output", "status", "command", "batch_script", "pid", "sbatch_options", "slurm_jobid"],
    )
    parser_show.set_defaults(handler=show_handler)

    parser_show_server_log = subparsers.add_parser("show-server-log", help="Shows the log of CommandPoolServer")
    parser_show_server_log.add_argument("group_id")
    parser_show_server_log.add_argument(
        "--line",
        "-n",
        type=int,
        default=1000,
        help="Show the given number of last lines",
    )
    parser_show_server_log.set_defaults(handler=show_server_log_handler)

    parser_tpl = subparsers.add_parser("tpl", help="Generates a template shell script")
    parser_tpl.add_argument("group_id", nargs="?")
    parser_tpl.set_defaults(
        handler=partial(
            tpl_handler,
            parser_start=parser_start,
            parser_add=parser_add,
            parser_dump=parser_dump,
            parser_run=parser_run,
        )
    )

    args = parser.parse_args(sysargs)
    if hasattr(args, "handler"):
        args.handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
