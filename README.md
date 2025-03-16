# Pybg: Parallel job management tool
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/kamo-naoyuki/pybg/main.svg)](https://results.pre-commit.ci/latest/github/kamo-naoyuki/pybg/main)

## What is this?
In Unix shell scripts, commands can be executed in parallel using background processes, but pybg simplifies job management with additional features.

Key features:
- Monitoring of job success and failure
- Management of job output logs
- Controling of the number of concurrently running jobs
- Re-execution of failed jobs
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

### Template generation

The basic usage of Pybg is somewhat formulaic or template-like. To simplify this, Pybg supports generating a shell script that describes these fundamental usage steps.

```sh
pybg tpl > run.sh
```

### Run only failed jobs

The most important feature of Pybg is its ability to manage job success and failure.
If some of the jobs you submitted succeed while others fail, you will need to rerun only the failed jobs.

With Pybg, you can easily retry failed jobs without any extra effort.


```sh
pybg run group_id
```

If you want to rerun all jobs, including those that succeeded, set the `--rerun` option to true.


```sh
pybg run --rerun true group_id
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

- Showing the jobids of failed/succeeded/unfinished jobs

```sh
pybg show <group-id> <failed/succeeded/unfinished>
```
### Suppor for Slurm Workload Manager

Pybg supports job submission via Slurm Workload Manager.
While it is possible to submit jobs by directly calling `sbatch` or `srun` from Pybg, these commands cannot be terminated if Pybg exits abnormally.

<table>
<tr>
<th>Direct submission via `srun`</th>
<th>Using `--slurm-option`</th>
<th>Adding `#SBATCH`, which is equivalent to `--slurm-option`</th>
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
pybg add --slurm-option "-p slurm_partition -c 3" sleep 10
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


Also, pay attention to the command at the far right.
When using `--slurm-option <option>` followed by `pybg dump`, you should see that the command in the commands list includes `#SBATCH <option>`.
In fact, adding `#SBATCH <option>` with `pybg add` will produce exactly the same behavior.
