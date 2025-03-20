# Pybg: Local job management tool
[![Lint](https://github.com/kamo-naoyuki/pybg/actions/workflows/lint.yml/badge.svg)](https://github.com/kamo-naoyuki/pybg/actions/workflows/lint.yml)
[![Pytest](https://github.com/kamo-naoyuki/pybg/actions/workflows/pytest.yml/badge.svg)](https://github.com/kamo-naoyuki/pybg/actions/workflows/pytest.yml)
[![codecov](https://codecov.io/gh/kamo-naoyuki/pybg/graph/badge.svg?token=820U4qvFg1)](https://codecov.io/gh/kamo-naoyuki/pybg)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/kamo-naoyuki/pybg/main.svg)](https://results.pre-commit.ci/latest/github/kamo-naoyuki/pybg/main)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## What is this?
Pybg executes jobs in parallel as background processes, similar to Unix shell background processes, but with additional features.

Key features:
- Monitoring of job success and failure
- Management of job output logs
- Control of the number of concurrently running jobs
- Automatic re-execution of failed jobs
- Support for job submission via `Slurm Workload Manager`

## Install

```sh
pip install git+https://github.com/kamo-naoyuki/pybg
```

> [!CAUTION]
> Windows is not supported

## Example

<table>
<tr>
<th>Unix background process</th>
<th>Pybg style</th>
</tr>
<tr>
<td>
<sub>

```sh
#!/bin/sh
echo Hello World &
sleep 40 &
python -c "print('This is python')" &
wait
```

</sub>
<td>
<sub>

```sh
#!/bin/sh
# 1. Start the command pool server or clear all commands
pybg start group_id
# 2. Register jobs to the server
pybg add group_id echo Hello World
pybg add group_id sleep 40
pybg add group_id python -c "print('This is python')"
# 3. Dump commands to a text file and stop the server
pybg write group_id
# 4. Run commands
pybg run group_id
```

</sub>
</td>
</tr>
</table>

Please confirm that the list of commands registered under `pybg/group_id/commands` is correctly recorded.

```sh
cat pybg/group_id/commands
```

`pybg start`, `pybg add`, and `pybg dump` are merely support commands for creating this text file.
Once this file exists, jobs can be executed using only `pybg run`.

This means that users can manually edit the text file, allowing for fine-grained modifications to jobs.

## Template generation

The basic usage of Pybg follows a consistent template. To simplify this, Pybg can generate a shell script with the essential steps.

```sh
pybg tpl > run.sh
```

## Run only nonsuccess/failed/unfinished/success jobs

One of Pybg's most important features is its ability to manage job success and failure.
If some jobs succeed while others fail, you can rerun only the failed jobs.

```sh
pybg run group_id <nonsuccess|fail|unfinish|success>
```

- `fail` indicates jobs that exited with a non-zero status.
- `unfinish` indicates jobs that have not yet started.
- `nonsuccess` includes both `fail` and `unfinish` jobs.
- `success` indicates jobs that exited with a zero status.

You can abbreviate these options by using only the first character.

```sh
pybg run group_id n  # "n" is equivalent to "nonsuccess"
```

You can also specify job IDs to be executed directly.

```sh
pybg run group_id jobid1 jobid2 ...
```

## Automatic resubmission of failed jobs

If a job fails, it can be automatically resubmitted. You can specify the number of resubmission attempts as shown below.
By default, this value is set to 0, meaning no resubmission.

```sh
pybg run --retry 3 run group_id
```

To resubmit jobs indefinitely, use `-1`:

```sh
pybg run --retry -1 run group_id
```

## Showing status

- View the output of a job

```sh
pybg show <group-id> <jobid>
```

- View the exit status of a finished job

```sh
pybg show <group-id> <jobid> status
```

- View the command associated with a job

```sh
pybg show <group-id> <jobid> command
```

- View the job IDs in a job group

```sh
pybg show <group-id>
```

- View job IDs for `notsuccess|fail|success|unfinish` jobs

```sh
pybg show <group-id> <notsuccess|fail|success|unfinish>
```

## To change basedir

By default, Pybg uses `./pybg_logs` as the output directory. You can change the directory using the `--basedir` option.

```sh
pybg --basedir <basedir> dump group_id
pybg --basedir <basedir> run group_id
pybg --basedir <basedir> show group_id
```

Alternatively, you can specify the directory using the `PYBG_LOGS` environment variable.

```sh
export PYBG_LOGS=<basedir>
pybg dump group_id
pybg run group_id
pybg show group_id
```

## Support for Slurm Workload Manager

Pybg supports job submission via the Slurm Workload Manager.

<table>
<tr>
<th>Direct submission via `srun`</th>
<th>Using `--sbatch-options`</th>
<th>Adding `#SBATCH`, which is equivalent to `--sbatch-options`</th>
</tr>
<tr>
<td>
<sub>

```sh
pybg add srun -p slurm_partition -c 3 sleep 10
```

</sub>
<td>
<sub>

```sh
pybg add --sbatch-options "-p slurm_partition -c 3" sleep 10
```

</sub>
</td>
<td>
<sub>

```sh
pybg add sleep 10 "#SBATCH -p slurm_partition -c 3"
```

</sub>
</td>
</tr>
</table>

As shown in the leftmost example, you can use `srun` or `sbatch` directly.
However, if Pybg exits abnormally, it may not be able to cancel these jobs.
For job submission via Slurm, using `--sbatch-options` is recommended.

When using `--sbatch-options <option>` followed by `pybg dump`, the resulting commands list will include `#SBATCH <option>`.
In fact, adding `#SBATCH <option>` with `pybg add` achieves the same behavior.

> [!TIP]
> When Pybg is terminated, it tries to cancel Slurm jobs.

## Tips: Using shell control operators

If you want to use shell control operators like `;`, `&&`, `||`, etc., you should wrap the command using `sh -c`.

Incorrect:

```sh
pybg add echo AAA; exit 0
```

Correct:

```sh
pybg add sh -c "echo AAA; exit 0"
```
