import traceback
import signal
import argparse
from collections import Counter
import datetime
import enum
from functools import partial
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
import select
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

import colorama

colorama.init(autoreset=True)

RED = colorama.Fore.RED
GREEN = colorama.Fore.GREEN
RESET = colorama.Style.RESET_ALL

use_color = sys.stdout.isatty()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
FORMAT = "%(levelname)-9s  %(asctime)s [%(filename)s:%(lineno)d] %(message)s"
st_handler = logging.StreamHandler()
st_handler.setFormatter(logging.Formatter(FORMAT))
logger.addHandler(st_handler)

dfmt = "%Y/%m/%d %H:%M:%S.%f"


def print_colored(text, *, color=None, **kwargs):
    if use_color and color is not None:
        print(color + text + RESET, **kwargs)
    else:
        print(text, **kwargs)


class LockPrint:
    def __init__(self):
        self.lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self.lock:
            print_colored(*args, **kwargs)


def read_file_reverse(filename, max_lines):
    lines = []
    with open(filename, "rb") as f:
        f.seek(0, 2)
        position = f.tell()
        buffer = []
        line_count = 0

        while position > 0 and line_count < max_lines:
            position -= 1
            f.seek(position)
            char = f.read(1)

            if char == b"\n":
                if buffer:
                    lines.append(b"".join(reversed(buffer)).decode())
                    buffer = []
                    line_count += 1
                    if line_count >= max_lines:
                        break
            else:
                buffer.append(char)

        if buffer and line_count < max_lines:
            lines.append(b"".join(reversed(buffer)).decode("utf-8"))
    for line in lines[::-1]:
        print(line)


class PROTO:
    ADD = b"\1"
    CLEAR = b"\2"
    DUMP = b"\3"
    END_COMMAND = b"\4"


class CommandPoolServer:
    @classmethod
    def get_socket(cls):
        if os.name == "posix":
            return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    @classmethod
    def get_socket_file(cls, group_id):
        if os.name == "posix":
            return f"/tmp/pybg-{os.getuid()}/{group_id}"
        else:
            raise RuntimeError("Only UNIX is supported")
            if isinstance(group_id, int):
                raise RuntimeError(
                    f"group_id must be a port number for Windows: {group_id}"
                )
            return ("127.0.0.1", group_id)

    @classmethod
    def get_log_file(cls, group_id):
        return f"pybg-{os.getuid()}/{group_id}.log"

    def __init__(self, group_id, timeout=60 * 5):
        self.group_id = group_id
        self.socket_file = CommandPoolServer.get_socket_file(group_id)
        Path(self.socket_file).parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.last_access_time = time.time()
        self.running = True
        self.lock = threading.Lock()
        self.log_file = CommandPoolServer.get_log_file(group_id)
        self.commands = []

    def daemonize(self):
        if os.fork() > 0:
            sys.exit()
        os.setsid()
        if os.fork() > 0:
            sys.exit()
        sys.stdin = open("/dev/null", "r")
        Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(self.log_file, "a+")
        sys.stderr = open(self.log_file, "a+")

    def is_port_in_use(self):
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

    def start(self):
        self.daemonize()

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

            sockets_list = [server]
            write_flagup = set()
            received = defaultdict(lambda: b"")

            while self.running:
                readable, writable, exceptional = select.select(
                    sockets_list, sockets_list, []
                )

                finished = set()
                for s in readable:
                    if s is server:
                        client_socket, addr = server.accept()
                        client_socket.setblocking(False)
                        sockets_list.append(client_socket)
                    else:
                        try:
                            data = s.recv(1024)
                        except BlockingIOError:
                            pass
                        else:
                            with self.lock:
                                self.last_access_time = time.time()
                            if not data:
                                finished.add(s)
                            if PROTO.DUMP in data:
                                logger.info("RECV: DUMP")
                                write_flagup.add(s)
                            received[s] += data

                for s in finished:
                    if received[s].startswith(PROTO.CLEAR):
                        self.commands = []
                        logger.info("RECV: CLEAR")

                    elif received[s].startswith(PROTO.ADD):
                        self.commands.append(received[s][len(PROTO.ADD) :])
                        logger.info("RECV: ADD")

                    elif received[s].startswith(PROTO.DUMP):
                        pass

                    elif received[s]:
                        logger.error(f"Unknown format: {received[s]}")
                    else:
                        pass

                    s.close()
                    del received[s]
                    sockets_list.remove(s)

                for s in writable:
                    if s not in write_flagup:
                        continue
                    error = False
                    for command in self.commands:
                        try:
                            s.sendall(command + b"\n")
                        except BlockingIOError:
                            error = True
                            logger.error("Socket is not writable")
                    try:
                        s.sendall(PROTO.END_COMMAND)
                    except BlockingIOError:
                        error = True
                        logger.error("Socket is not writable")

                    if not error:
                        with self.lock:
                            self.last_access_time = time.time()

                    write_flagup.remove(s)
                    with self.lock:
                        logger.error(f"Stopping server...")
                        self.running = False

                for s in exceptional:
                    logger.error(f"Error on connection happened")
                    if s in received:
                        del received[s]
                    if s in write_flagup:
                        write_flagup.remove(s)
                    s.close()
                    sockets_list.remove(s)


def server_start(group_id, timeout=10):
    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        if client.connect_ex(socket_file) != 0:
            if os.name == "posix" and Path(socket_file).exists():
                try:
                    Path(socket_file).unlink()
                except Exception:
                    pass

            server = CommandPoolServer(group_id=group_id)
            process = multiprocessing.Process(target=server.start, daemon=True)
            process.start()

    # Wait until the server has started
    start_time = time.time()
    with CommandPoolServer.get_socket() as client:
        while client.connect_ex(socket_file) != 0:
            if time.time() - start_time > timeout:
                raise RuntimeError(
                    f"Server '{group_id}' is not running? Timeout: {timeout}s"
                )
            time.sleep(0.01)


def dump(group_id, basedir, timeout=1, allow_same=False):
    # Wait until the server has started
    socket_file = CommandPoolServer.get_socket_file(group_id)
    start_time = time.time()
    with CommandPoolServer.get_socket() as client:
        while client.connect_ex(socket_file) != 0:
            if time.time() - start_time > timeout:
                raise RuntimeError(
                    f"Server '{group_id}' is not runnig? Timeout: {timeout}s"
                )
            time.sleep(0.01)

    basedir = Path(basedir)
    groupdir = basedir / group_id
    groupdir.mkdir(parents=True, exist_ok=True)

    added_commands = set()

    with (groupdir / "commands").open(
        "w"
    ) as fout, CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.DUMP)

        received = b""
        count = 0
        while True:
            data = client.recv(1024)
            if not data:
                raise RuntimeError(f"Server is shutdown")

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

            for idx, command in enumerate(binary_command_list):
                decoded_command = command.decode()
                if "#SBATCH" in decoded_command:
                    valid_command, slurm_options = decoded_command.split(
                        "#SBATCH", maxsplit=1
                    )
                else:
                    valid_command = decoded_command
                valid_command = valid_command.strip()

                if valid_command not in added_commands:
                    fout.write(f"{decoded_command}\n")
                    count += 1
                else:
                    print_colored(f"Duplicated commands: {valid_command}", color=RED)

                if not allow_same:
                    added_commands.add(valid_command)

            if PROTO.END_COMMAND in received:
                if received != PROTO.END_COMMAND:
                    raise RuntimeError(f"Bug?: {received}")
                # Break while loop
                print_colored(f"Dump {count} commands in {groupdir / 'commands'}")
                return


class Runner:
    def __init__(self):
        self.subprocesses = []

    def write_status(
        self,
        group_id,
        failed_jobs,
        processes,
        lock,
        count,
        count_lock,
        lock_print,
        event,
        waittime=0.02,
        file=sys.stdout,
        squeue_minimum_interval=5,
    ):

        last_squeue_time = {}
        while True:
            with lock:
                _processes = processes.copy()
            finished = set()
            for jobid, (process, jobdir, slurm_jobid) in _processes.items():
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
                    if not (jobdir / "status").exists():
                        # If active_text is existing, the job is runnig
                        if (jobdir / "active").exists():

                            time_from_last_update = time.time() - (jobdir / "active").stat().st_mtime
                            if time_from_last_update > 10:
                                # Force sync
                                list(jobdir.iterdir())

                                # the job has been Killed
                                if not (jobdir / "status").exists():
                                    (jobdir / "status").write_text("1\n")
                                    returncode = 1
                                else:
                                    try:
                                        returncode = int((jobdir / "status").read_text().strip())
                                    except:
                                        returncode = 1

                        # if active_text is not existing, the job has not yet started
                        else:
                            # Get status using squeue command
                            last_time = last_squeue_time.get(slurm_jobid)
                            # To reduce the load of slurm server, avoid excecuting squeue command within squeue_minimum_interval
                            if last_time is None or time.time() - last_time > squeue_minimum_interval:
                                process = subprocess.Popen(["squeue", "--noheader", "--job", str(slurm_jobid), "-o", "%T"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                                last_squeue_time[slurm_jobid] = time.time()
                                stdout, stderr = process.communicate()
                                if process.returncode != 0:
                                    # Force sync
                                    list(jobdir.iterdir())
                                    # Job already finished
                                    if "Invalid job id specified" in stderr:
                                        lock_print(
                                            f"[bg|{group_id}|Error|{datetime.datetime.now():{dfmt}}]"
                                            f" squeue command is enexpectedly failed: {stderr}",
                                            file=file,
                                            color=RED,
                                        )

                                    # the job has been Killed
                                    if not (jobdir / "status").exists():
                                        (jobdir / "status").write_text("1\n")
                                        returncode = 1
                                    else:
                                        try:
                                            returncode = int((jobdir / "status").read_text().strip())
                                        except:
                                            returncode = 1

                    else:
                        try:
                            returncode = int((jobdir / "status").read_text().strip())
                        except:
                            returncode = 1

                if returncode is not None:
                    (Path(jobdir) / "status").write_text(f"{returncode}\n")
                    finished.add(jobid)
                    if returncode != 0:
                        lock_print(
                            f"[bg|{group_id}|Fail({returncode})|{datetime.datetime.now():{dfmt}}]"
                            f" pybg show {group_id} {jobid}",
                            file=file,
                            color=RED,
                        )
                        with count_lock:
                            count[1] += 1
                        failed_jobs.add(jobid)
                    else:
                        lock_print(
                            f"[bg|{group_id}|Succeed|{datetime.datetime.now():{dfmt}}]"
                            f" pybg show {group_id} {jobid}",
                            file=file,
                            color=GREEN,
                        )
                        with count_lock:
                            count[0] += 1

            with lock:
                for jobid in finished:
                    del processes[jobid]

            time.sleep(waittime)

            if event.is_set():
                break

    def run_process(
        self,
        group_id,
        processes,
        jobs,
        lock,
        count,
        count_lock,
        lock_print,
        event,
        log_interval=300,
        num_parallel=10,
        launch_interval=0.1,
        file=sys.stdout,
    ):
        st = time.time()
        while True:
            if len(jobs) > 0 and len(processes) <= num_parallel:
                command, jobdir, slurm_options = jobs.pop()
                logfile = Path(jobdir) / "output"
                jobid = Path(jobdir).name
                if slurm_options is None:
                    flogfile = logfile.open("w")
                    try:
                        process = subprocess.Popen(
                            shlex.split(command), stdout=flogfile, stderr=flogfile
                        )
                    except FileNotFoundError as e:
                        flogfile.write(traceback.format_exc() + "\n")
                        lock_print(
                            f"[bg|{group_id}|Error|{datetime.datetime.now():{dfmt}}]"
                            f"Failed to execute command: {command}",
                            file=file,
                            color=RED,
                        )
                        with count_lock:
                            count[1] += 1
                    else:
                        slurm_jobid = None
                        self.subprocesses.append((process, slurm_jobid))

                        lock_print(
                            f"[bg|{group_id}|Submit(pid={process.pid},jobid={jobid})|{datetime.datetime.now():{dfmt}}]"
                            f" {command}",
                            file=file,
                        )
                        with lock:
                            processes[jobid] = (process, jobdir, slurm_jobid)

                else:
                    # Using sbatch (Slurm batch script)
                    process = subprocess.Popen(
                        ["sbatch", "-o", logfile] + shlex.split(slurm_options), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                    active_text = jobdir / "active"
                    status_text = jobdir / "status"
                    for p in [active_text, status_text]:
                        if p.exists():
                            try:
                                p.unlink()
                            except Exception:
                                pass
                    batch_script = f"""\
#!/usr/bin/env sh
update_timestamp() {{
    while true; do
        touch '{active_text}'
        sleep 5
    done

}}
write_status() {{
    echo $? > '{status_text}'
    kill $PID
}}
# For logging
echo '# {slurm_options}'

rm -f '{status_text}'
# Force sync
ls '{jobdir}' &> /dev/null

update_timestamp &
PID=$!
trap write_status SIGINT
trap write_status SIGTERM
trap write_status SIGQUIT
trap write_status EXIT

{command}
"""
                    (jobdir / "batch_script").write_text(batch_script)
                    stdout, stderr = process.communicate(batch_script.encode())
                    # Failed to submit
                    if process.returncode != 0:
                        lock_print(
                            f"[bg|{group_id}|Error(jobid={jobid})|{datetime.datetime.now():{dfmt}}]"
                            f" {stderr}",
                            color=RED,
                            file=file,
                        )
                    else:
                        def get_jobid(stdout):
                            # Derive jobid from output
                            # e.g. Submitted batch job 1346323
                            slurm_jobid = None
                            for token in stdout.strip().split():
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
                            lock_print(
                                f"[bg|{group_id}|Error(jobid={jobid})|{datetime.datetime.now():{dfmt}}]"
                                f"Unexpected output from sbatch: {stderr}",
                                file=file,
                            )
                        else:
                            self.subprocesses.append((None, slurm_jobid))
                            jobid = Path(jobdir).name
                            with lock:
                                processes[jobid] = (None, jobdir, slurm_jobid)

                            lock_print(
                                f"[bg|{group_id}|Submit(jobid={jobid},slurm_jobid={slurm_jobid})|{datetime.datetime.now():{dfmt}}]"
                                f" {command} #SBATCH {slurm_options}",
                                file=file,
                            )

            # All jobs are finished
            if len(jobs) == 0 and len(processes) == 0:
                event.set()
                break

            if time.time() - st > log_interval:
                with count_lock:
                    succeeded, failed = count
                queue = len(jobs)
                with lock:
                    running = len(processes)

                lock_print(
                    f"[bg|{group_id}|Status|{datetime.datetime.now():{dfmt}}]"
                    f" queue/running/succeeded/failed={queue}/{running}/{succeeded}/{failed}",
                    file=file,
                )
                st = time.time()
            time.sleep(launch_interval)

    def run(
        self,
        group_id,
        basedir="pybg",
        rerun=False,
        num_parallel=10,
        launch_interval=0.1,
        waittime=0.02,
        log_interval=30,
        file=sys.stdout,
    ):

        basedir = Path(basedir)
        groupdir = basedir / group_id

        if (groupdir / "commands").exists():
            with (groupdir / "commands").open("r") as f:
                command_list = [line.strip() for line in f]
        else:
            raise RuntimeError(f'{str(groupdir / "commands")} is not existing')

        lock_print = LockPrint()

        jobs = []
        counter = Counter()
        for command in command_list:
            if "#SBATCH" in command:
                for scommand in ["sbatch", "squeue"]:
                    if shutil.which(scommand) is None:
                        raise RuntimeError(f"Slurm is not setup? command not found: {scommand}")
                valid_command, slurm_options = command.split(
                    "#SBATCH", maxsplit=1
                )
                slurm_options = slurm_options.strip()
            else:
                valid_command = command
                slurm_options = None
            valid_command = valid_command.strip()
            counter[valid_command] += 1

            jobid = hashlib.sha256(
                (
                    valid_command + ""
                    if counter[valid_command] == 1
                    else str(counter[valid_command])
                ).encode()
            ).hexdigest()
            # Using the first 8 chars for usabilily
            jobid = jobid[:8]
            jobdir = groupdir / jobid
            jobdir.mkdir(parents=True, exist_ok=True)
            command_text = jobdir / "command"
            command_text.write_text(valid_command + "\n")
            status_text = jobdir / "status"
            slurm_options_text = jobdir / "slurm_options"
            if slurm_options is not None:
                slurm_options_text.write_text(slurm_options)
            elif slurm_options_text.exists():
                try:
                    slurm_options_text.unlink()
                except Exception:
                    pass

            if status_text.exists():
                try:
                    status = int(status_text.read_text())
                except:
                    status = -1
            else:
                status = -1

            if rerun or status != 0:
                jobs.append((valid_command, jobdir, slurm_options))
            else:
                lock_print(
                    f"[bg|{group_id}|Skip(jobid={jobid})|{datetime.datetime.now():{dfmt}}]"
                    f" {valid_command}",
                    file=file,
                )
        del counter

        lock = threading.Lock()
        count_lock = threading.Lock()
        count = [0, 0]
        event = threading.Event()
        processes = dict()
        failed_jobs = set()

        thread = threading.Thread(
            target=self.write_status,
            kwargs=dict(
                group_id=group_id,
                processes=processes,
                failed_jobs=failed_jobs,
                lock=lock,
                count=count,
                count_lock=count_lock,
                lock_print=lock_print,
                event=event,
                waittime=waittime,
                file=file,
            ),
            daemon=True,
        )

        thread.start()
        self.run_process(
            group_id=group_id,
            processes=processes,
            jobs=jobs,
            lock=lock,
            count=count,
            count_lock=count_lock,
            lock_print=lock_print,
            event=event,
            log_interval=log_interval,
            num_parallel=num_parallel,
            launch_interval=launch_interval,
            file=file
        )
        thread.join()

        succeeded, failed = count
        already_finished = len(command_list) - len(jobs)
        if already_finished != 0:
            if already_finished == 1:
                sub = "job have"
            else:
                sub = "jobs has"
            lock_print(
                f"[bg|{group_id}|End|{datetime.datetime.now():{dfmt}}]"
                f" {already_finished} {sub} already finished",
                file=file,
            )

        if failed != 0:
            if failed == 1:
                sub = "job has"
            else:
                sub = "jobs have"
            lock_print(
                f"[bg|{group_id}|End|{datetime.datetime.now():{dfmt}}]"
                f" {failed}/{failed + succeeded} {sub} been failed: ",
                file=file,
                color=RED,
            )
            failed_message = " ".join(failed_jobs)
            lock_print(failed_message, file=file, color=RED)
        elif succeeded != 0:
            if failed + succeeded == 1:
                sub = "job has"
            else:
                sub = "jobs have"
            lock_print(
                f"[bg|{group_id}|End|{datetime.datetime.now():{dfmt}}]"
                f" {failed + succeeded} {sub} successfully finished",
                file=file,
                color=GREEN,
            )


def clear_handler(args):
    start_and_clear(group_id=args.group_id)


def start_and_clear(group_id):
    server_start(group_id)

    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.CLEAR)


def add_handler(args):
    command = shlex.join(args.command)
    if args.slurm_options is not None:
        command += f" #SBATCH {args.slurm_options}"
    add(group_id=args.group_id, command=command)


def add(group_id, command):
    server_start(group_id)

    socket_file = CommandPoolServer.get_socket_file(group_id)
    with CommandPoolServer.get_socket() as client:
        client.connect(socket_file)
        client.sendall(PROTO.ADD + command.encode())


def dump_handler(args):
    dump(group_id=args.group_id, basedir=args.basedir, allow_same=args.allow_same)


def sig_handler(signum, frame, exit_status=1) -> None:
    sys.exit(exit_status)


def stop_processes(processes, timeout=30):
    for p, slurm_jobid in processes:
        if p is not None:
            # NOTE: send_signal is non-blocking
            # NOTE: Do nothing if the process completed.
            p.send_signal(signal.SIGINT)

    for p, slurm_jobid in processes:
        if p is not None:
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                print_colored(
                    f"[bg|Clear|{datetime.datetime.now():{dfmt}}] Process(pid={p.pid}) has not yet been stopped. Killing the process...",
                    color=RED,
                    flush=True,
                )
                p.kill()

    # No wait for killing


def run_handler(args):
    runner = Runner()
    try:
        runner.run(
            group_id=args.group_id,
            basedir=args.basedir,
            rerun=args.rerun,
            num_parallel=args.num_parallel,
            launch_interval=args.launch_interval,
            waittime=args.waittime,
            log_interval=args.log_interval,
        )
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop_processes(runner.subprocesses)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        raise


def show_server_log_handler(args):
    logfile = Path(CommandPoolServer.get_log_file(args.group_id))
    if logfile.exists():
        read_file_reverse(logfile, args.line)
        print(f"({logfile})")
    else:
        print(f"(Logfile is not existing: {logfile})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    parser_clear = subparsers.add_parser("clear")
    parser_clear.add_argument("group_id")
    parser_clear.set_defaults(handler=clear_handler)

    parser_add = subparsers.add_parser("add")
    parser_add.add_argument("--slurm-options", "-s", help="")
    parser_add.add_argument("group_id")
    parser_add.add_argument("command", nargs=argparse.REMAINDER)
    parser_add.set_defaults(handler=add_handler)

    parser_dump = subparsers.add_parser("dump")
    parser_dump.add_argument("group_id")
    parser_dump.add_argument("--basedir", default="pybg")
    parser_dump.add_argument("--allow-same", "-a", action="store_true", help="")
    parser_dump.set_defaults(handler=dump_handler)

    parser_run = subparsers.add_parser("run")
    parser_run.add_argument("group_id")
    parser_run.add_argument("--basedir", default="pybg")
    parser_run.add_argument("--rerun", "-r", action="store_true")
    parser_run.add_argument("--num-parallel", "-n", default=10, type=int)
    parser_run.add_argument("--launch-interval", default=0.1, type=float)
    parser_run.add_argument("--waittime", default=0.02, type=float)
    parser_run.add_argument("--log_interval", default=30.0, type=float)
    parser_run.set_defaults(handler=run_handler)

    parser_show_server_log = subparsers.add_parser("show-server-log")
    parser_show_server_log.add_argument("group_id")
    parser_show_server_log.add_argument(
        "--line",
        "-n",
        type=int,
        default=1000,
        help="Show the given number of last lines",
    )
    parser_show_server_log.set_defaults(handler=show_server_log_handler)

    args = parser.parse_args()
    if hasattr(args, "handler"):
        args.handler(args)
    else:
        parser.print_help()
