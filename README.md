# Pybg: Local job management tool
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/kamo-naoyuki/pybg/main.svg)](https://results.pre-commit.ci/latest/github/kamo-naoyuki/pybg/main)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)


## What is this?
As background processes of Unix shell, pybg can execute jobs in parallel with additional features.

Key features:
- Monitoring of job success and failure
- Management of job output logs
- Controling of the number of concurrently running jobs
- Automatic re-execution of failed jobs
- Support for job submission via `Slurm Workload Manage`

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
# 1. Start command pool server or clear all commands
pybg start group_id
# 2. Register jobs to the server
pybg add group_id echo Hello World
pybg add group_id sleep 40
pybg add group_id python -c "print('This is python')"
# 3. Dump commands in text and stop the server
pybg write group_id
# 4 Run commands
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
In reality, once this file exists, jobs can be executed using only `pybg run`.

This means that users can edit the text file manually, allowing for fine-grained modifications to jobs later.

## Template generation

The basic usage of Pybg is somewhat formulaic or template-like. Pybg supports generating a shell script that describes these fundamental usage steps.

```sh
pybg tpl > run.sh
```


## Run only nonsuccess/failed/unfinished/success jobs

The most important feature of Pybg is its ability to manage job success and failure.
If some of the jobs you submitted succeed while others fail, you will need to rerun only the failed jobs.

With Pybg, you can easily retry failed jobs without any extra effort.


```sh
pybg run group_id [nonsuccess|fail|unfinish|success]
```

`fail` indictes jobs which has exited with non-zero status, `unfinish` indicates jobs which have not yet started, `nonsuccess` indicates jobs corresponding to both `fail` and `unfinish`, and `success` indicates jobs which has exited  with zero status.

You can omit characters after the first one.


```sh
pybg run group_id n  # Equivalent to nonsuccess
```

You can also specify dirctly jobids to be executed

```sh
pybg run group_id jobid1 jobid2 ...
```


## Automatic submittion for failed jobs

If a job ends with failed status, it can be automatically resubmitted. You can specify the number of times to resubmit the job, as shown below. By default, it is set to 0, meaning no resubmission will occur.

```sh
pybg run --retry 3 run group_id
```

If -1 is specified, the job will be resubmitted indefinitely.

```sh
pybg run --retry -1 run group_id
```


## Showing status

- Showing the output of the job


```sh
pybg show <group-id> <jobid>
```

- Showing the exit-status of the job if the job has been finished


```sh
pybg show <group-id> <jobid> status
```

- Showing the command of the job

```sh
pybg show <group-id> <jobid> command
```

- Showing the jobids of the jobs-group

```sh
pybg show <group-id>
```

- Showing the jobids of notsuccess/fail/success/unfinish jobs

```sh
pybg show <group-id> <notsuccess|fail/success/unfinish>
```

## To change basedir

By default, Pybg uses `./pybg_logs` for the output directory. You can change the directory by `--basedir` option.

```sh
pybg --basedir <basedir> dump group_id
pybg --basedir <basedir> run group_id
pybg --basedir <basedir> show group_id
```

You can also specify the directory by `PYBG_LOGS` environment variable.


```sh
export PYBG_LOGS=<basedir>>
pybg dump group_id
pybg run group_id
pybg show group_id
```

## Suppor for Slurm Workload Manager

Pybg supports job submission via Slurm Workload Manager.

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


As shown in the command on the left, you can use srun or sbatch directly. However, in this case, if Pybg exits abnormally, it may not be able to cancel these jobs.

`--sbatch-options` is recommended for job submission via slurm.


Also, pay attention to the command at the far right in the above table.
When using `--sbatch-options <option>` followed by `pybg dump`, you should see that the command in the commands list includes `#SBATCH <option>`.
In fact, adding `#SBATCH <option>` with `pybg add` will produce exactly the same behavior.

> [!TIP]
> When Pybg is terminated, Pybg tries to cancel Slurm jobs

## Tips: To use controling operator of Unix shell
If you'd like to use some controling operator, such `;`, `&&`, `||`, etc. in a command, please use shell command with `-c`.

<table>
<tr>
<th>Bad</th>
<th>Good</th>
</tr>
<tr>
<td>
<sub>

```sh
pybg add echo AAA; exit 0
```

</sub>
<td>
<sub>

```sh
pybg add sh -c "echo AAA; exit 0"
```
</sub>
</td>
</tr>
</table>



